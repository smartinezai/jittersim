"""Small HTTP server for the jittersim control plane."""

import argparse
import hmac
import json
import logging
import os
import ssl
import time
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from jittersim.config.models import (
    DataLimit,
    Device,
    DeviceGroup,
    DomainObservation,
    GroupDataLimit,
    Impairment,
    MacPolicy,
    Profile,
    Progression,
    Randomness,
    RuntimeState,
    Schedule,
    ServiceQuota,
    TimeQuota,
    Timer,
    WebsitePolicy,
    normalize_domain,
    time_quota_counter_id,
)
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import PlanError
from jittersim.engine.progression import progression_factor
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperClient, HelperClientError

LOGGER = logging.getLogger(__name__)
MAX_JSON_BODY_BYTES = 64 * 1024
WRITE_PATHS = frozenset(
    (
        "/api/profiles",
        "/api/devices",
        "/api/groups",
        "/api/groups/delete",
        "/api/schedules",
        "/api/timers",
        "/api/timers/cancel",
        "/api/progressions",
        "/api/progressions/stop",
        "/api/data-limits",
        "/api/data-limits/reset",
        "/api/group-data-limits",
        "/api/group-data-limits/reset",
        "/api/service-quotas",
        "/api/service-quotas/reset",
        "/api/time-quotas",
        "/api/time-quotas/reset",
        "/api/time-quotas/start",
        "/api/time-quotas/stop",
        "/api/policies",
        "/api/access-point",
        "/api/router/sync-ap",
        "/api/router/wifi",
        "/api/connected-devices/apply-profile",
        "/api/observations/scan",
        "/api/observations/add-to-profile",
        "/api/observations/add-global-block",
        "/api/apply",
        "/api/reset",
    )
)
ACTION_PATHS = frozenset(("/api/apply", "/api/reset", "/api/observations/scan"))
PROTECTED_READ_PATHS = frozenset(
    (
        "/api/action-history",
        "/api/backup",
        "/api/data-limits",
        "/api/diagnostics",
        "/api/group-data-limits",
        "/api/observations",
        "/api/service-quotas",
        "/api/time-quotas",
        "/api/traffic",
    )
)


# Raw string: JavaScript escapes such as "\n" must reach the browser unchanged.
DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>jittersim</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b1220;
      color: #e7eef8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0 auto;
      max-width: 72rem;
      padding: 1.5rem 1rem 4rem;
      background: radial-gradient(circle at top right, #15345a 0, #0b1220 34rem);
      line-height: 1.5;
    }
    h1, h2, h3, p { margin-top: 0; }
    h1 { margin-bottom: 0.25rem; font-size: clamp(2rem, 5vw, 3.2rem); letter-spacing: -0.04em; }
    h2 { margin-bottom: 0.75rem; font-size: 1.15rem; }
    h3 { margin: 1.5rem 0 0.75rem; font-size: 0.9rem; color: #a8c7e8; text-transform: uppercase; letter-spacing: 0.08em; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    pre {
      min-height: 5rem;
      margin: 0;
      overflow-x: auto;
      padding: 1rem;
      border: 1px solid #31445e;
      border-radius: 0.75rem;
      background: #08101c;
      color: #c7d7eb;
      font-size: 0.82rem;
    }
    .page-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1.25rem;
    }
    .eyebrow { margin-bottom: 0.35rem; color: #70b7ff; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.14em; }
    .subtitle { margin-bottom: 0; color: #a8b8ce; }
    .badge {
      flex: 0 0 auto;
      margin-top: 0.4rem;
      padding: 0.4rem 0.7rem;
      border: 1px solid #46627f;
      border-radius: 999px;
      background: #142338;
      color: #c6dbf4;
      font-size: 0.78rem;
      font-weight: 700;
      white-space: nowrap;
    }
    .badge.hardware { border-color: #2c9d73; background: #10372d; color: #8de2bc; }
    .badge.configuration { border-color: #a77b31; background: #3b2a12; color: #f5ca78; }
    .badge.pending { color: #a8b8ce; }
    .notice {
      margin-bottom: 1rem;
      padding: 0.8rem 1rem;
      border: 1px solid #355170;
      border-left: 0.3rem solid #70b7ff;
      border-radius: 0.6rem;
      background: #102238;
      color: #c8d9ec;
    }
    .card {
      margin: 1rem 0;
      padding: 1.25rem;
      border: 1px solid #263b55;
      border-radius: 0.9rem;
      background: rgba(18, 31, 49, 0.9);
      box-shadow: 0 0.9rem 2rem rgba(0, 0, 0, 0.16);
    }
    .action-bar {
      position: sticky;
      top: 0.75rem;
      z-index: 10;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 0.75rem 1rem;
      border-color: #3c6690;
      background: rgba(15, 31, 51, 0.96);
      box-shadow: 0 1rem 2rem rgba(0, 0, 0, 0.3);
      backdrop-filter: blur(0.75rem);
    }
    .action-copy p { margin-bottom: 0; color: #a8b8ce; }
    .runtime-summary { margin-top: 0.55rem; font-size: 0.88rem; font-weight: 700; color: #c4d5e8 !important; }
    .runtime-summary.applied, .runtime-summary.reset { color: #8de2bc !important; }
    .runtime-summary.error { color: #ff9e9e !important; }
    .runtime-summary.applying, .runtime-summary.resetting { color: #f5ca78 !important; }
    .action-buttons { display: flex; align-items: center; gap: 0.6rem; }
    button {
      margin-top: 1rem;
      padding: 0.62rem 0.9rem;
      border: 1px solid #46627f;
      border-radius: 0.55rem;
      background: #1a2d46;
      color: #edf5ff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: transform 120ms ease, filter 120ms ease, opacity 120ms ease;
    }
    button:hover:not(:disabled) { filter: brightness(1.18); transform: translateY(-1px); }
    button:focus-visible, input:focus-visible, .help-icon:focus-visible {
      outline: 2px solid #70b7ff;
      outline-offset: 2px;
    }
    button:disabled { cursor: wait; opacity: 0.55; }
    button.primary { border-color: #4c9fe8; background: #2479bd; }
    button.danger { border-color: #a9565d; background: #642b38; }
    .action-message {
      grid-column: 1 / -1;
      min-height: 1.5rem;
      margin: 0;
      padding: 0.65rem 0.8rem;
      border-radius: 0.5rem;
      color: #b9c9dc;
    }
    .action-message.success { background: #123b31; color: #8de2bc; }
    .action-message.error { background: #45222c; color: #ffb0b0; }
    .action-message.working { background: #3b2d15; color: #f5ca78; }
    .toast {
      position: fixed;
      top: 1rem;
      right: 1rem;
      z-index: 100;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 0.75rem;
      width: min(34rem, calc(100vw - 2rem));
      padding: 0.85rem 1rem;
      border: 1px solid #46627f;
      border-left: 0.3rem solid #70b7ff;
      border-radius: 0.7rem;
      background: #102238;
      color: #dcecff;
      box-shadow: 0 0.9rem 2rem rgba(0, 0, 0, 0.4);
    }
    .toast[hidden] { display: none; }
    .toast.success { border-color: #2c9d73; border-left-color: #70d7a9; background: #123b31; }
    .toast.error { border-color: #a9565d; border-left-color: #ff8585; background: #45222c; }
    .toast.working { border-left-color: #f5ca78; }
    .toast-icon { display: grid; place-items: center; width: 1.2rem; font-weight: 800; }
    .toast.working .toast-icon {
      width: 0.9rem;
      height: 0.9rem;
      border: 2px solid #6a5a37;
      border-top-color: #f5ca78;
      border-radius: 50%;
      animation: toast-spin 0.8s linear infinite;
    }
    .toast button { margin: 0; padding: 0.25rem 0.45rem; }
    button:active:not(:disabled) { transform: translateY(1px) scale(0.98); }
    button[aria-busy="true"] { position: relative; }
    button[aria-busy="true"]::after {
      display: inline-block;
      width: 0.72rem;
      height: 0.72rem;
      margin-left: 0.55rem;
      border: 2px solid currentColor;
      border-right-color: transparent;
      border-radius: 50%;
      content: "";
      vertical-align: -0.08rem;
      animation: toast-spin 0.8s linear infinite;
    }
    @keyframes toast-spin { to { transform: rotate(360deg); } }
    .content-grid, .data-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .content-grid .card, .data-grid .card { margin: 0; }
    .section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
    .section-heading p { margin: 0; color: #91a8c1; font-size: 0.82rem; }
    .count-badge { padding: 0.25rem 0.55rem; border: 1px solid #46627f; border-radius: 999px; color: #c6dbf4; font-size: 0.75rem; white-space: nowrap; }
    .connected-list { display: grid; gap: 0.6rem; }
    .connected-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto minmax(14rem, auto);
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem;
      border: 1px solid #2b435f;
      border-radius: 0.6rem;
      background: #0d1b2d;
    }
    .connected-dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; background: #70d7a9; box-shadow: 0 0 0 0.2rem #70d7a922; }
    .connected-address { margin: 0; color: #edf5ff; font-weight: 700; }
    .connected-meta { margin: 0.1rem 0 0; color: #91a8c1; font-size: 0.8rem; }
    .connected-state { color: #8de2bc; font-size: 0.78rem; font-weight: 700; text-transform: capitalize; }
    .connected-controls { display: flex; align-items: center; gap: 0.5rem; }
    .connected-controls select { min-width: 10rem; }
    .connected-controls button { margin-top: 0; white-space: nowrap; }
    select {
      padding: 0.55rem 0.6rem;
      border: 1px solid #3a526e;
      border-radius: 0.45rem;
      background: #0b1727;
      color: #edf5ff;
      font: inherit;
    }
    .device-empty { margin: 0; color: #91a8c1; }
    .connected-card { margin-top: 1rem; }
    .traffic-card { margin-top: 1rem; }
    .traffic-controls { display: grid; grid-template-columns: minmax(14rem, 0.75fr) minmax(0, 1.5fr); align-items: end; gap: 1rem; margin-top: 1rem; }
    .traffic-controls label { max-width: 24rem; }
    .traffic-status { margin: 0; color: #91a8c1; font-size: 0.82rem; }
    .traffic-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; margin-top: 1rem; }
    .traffic-metric { min-width: 0; padding: 0.75rem; border: 1px solid #2b435f; border-radius: 0.6rem; background: #0d1b2d; }
    .traffic-metric span { display: block; color: #91a8c1; font-size: 0.76rem; }
    .traffic-metric strong { display: block; margin-top: 0.25rem; color: #edf5ff; font-size: 1.05rem; overflow-wrap: anywhere; }
    .traffic-chart-wrap { margin-top: 1rem; padding: 0.65rem; border: 1px solid #2b435f; border-radius: 0.65rem; background: #0b1727; }
    #traffic-chart { display: block; width: 100%; height: 16rem; }
    .traffic-legend { display: flex; flex-wrap: wrap; gap: 1rem; margin-top: 0.65rem; color: #91a8c1; font-size: 0.78rem; }
    .traffic-legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
    .traffic-key { width: 0.75rem; height: 0.2rem; border-radius: 1rem; background: #70b7ff; }
    .traffic-key.download { background: #70d7a9; }
    .traffic-note { margin: 0.75rem 0 0; color: #91a8c1; font-size: 0.8rem; }
    .uplink-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.75rem; margin-top: 1rem; }
    .uplink-value { padding: 0.75rem; border: 1px solid #2b435f; border-radius: 0.6rem; background: #0d1b2d; }
    .uplink-value strong { display: block; color: #edf5ff; font-size: 0.95rem; overflow-wrap: anywhere; }
    .uplink-value span { display: block; margin-bottom: 0.2rem; color: #91a8c1; font-size: 0.76rem; }
    .button-link { display: inline-block; margin-top: 1rem; padding: 0.62rem 0.9rem; border: 1px solid #46627f; border-radius: 0.55rem; background: #1a2d46; color: #edf5ff; font-weight: 700; text-decoration: none; }
    .button-link:hover { filter: brightness(1.18); }
    .router-controls { display: grid; gap: 0.75rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #2b435f; }
    .router-controls .action-buttons { flex-wrap: wrap; }
    .router-controls .action-buttons button { margin-top: 0; }
    .observations-card { margin-top: 1rem; }
    .observation-actions { display: flex; align-items: center; gap: 0.75rem; }
    .observation-actions button { margin-top: 0; white-space: nowrap; }
    .observation-note { margin: 0.75rem 0 0; color: #91a8c1; font-size: 0.82rem; }
    .observation-list { display: grid; gap: 0.75rem; margin-top: 1rem; }
    .observation-device { padding: 0.85rem; border: 1px solid #2b435f; border-radius: 0.6rem; background: #0d1b2d; }
    .observation-device-heading { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
    .observation-device-heading h3 { margin: 0; color: #edf5ff; text-transform: none; letter-spacing: 0; font-size: 0.95rem; }
    .observation-device-heading p { margin: 0; color: #91a8c1; font-size: 0.78rem; }
    .observation-domain-list { display: grid; gap: 0.35rem; margin: 0.65rem 0 0; padding: 0; list-style: none; }
    .observation-domain { display: flex; justify-content: space-between; gap: 1rem; padding: 0.45rem 0.55rem; border-radius: 0.35rem; background: #102238; color: #c7d7eb; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }
    .observation-domain-count { color: #8de2bc; white-space: nowrap; }
    .policy-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .policy-grid > div { min-width: 0; }
    .policy-note, .form-note { margin: 0.7rem 0 0; color: #91a8c1; font-size: 0.82rem; }
    textarea { min-height: 5rem; resize: vertical; }
    details { margin-top: 1rem; border-top: 1px solid #2b435f; padding-top: 0.8rem; }
    summary { cursor: pointer; color: #a8c7e8; font-weight: 700; }
    .field-label { display: inline-flex; align-items: center; gap: 0.35rem; }
    .help-icon {
      position: relative;
      display: inline-grid;
      place-items: center;
      width: 1.05rem;
      height: 1.05rem;
      border: 1px solid #6483a4;
      border-radius: 50%;
      color: #b9d8f4;
      font-size: 0.7rem;
      font-weight: 800;
      line-height: 1;
      cursor: help;
    }
    .help-icon::after {
      position: absolute;
      top: calc(100% + 0.5rem);
      left: 0;
      z-index: 20;
      width: min(22rem, calc(100vw - 3rem));
      padding: 0.65rem 0.75rem;
      border: 1px solid #547392;
      border-radius: 0.45rem;
      background: #08101c;
      color: #dcecff;
      content: attr(data-help);
      font-size: 0.78rem;
      font-weight: 400;
      line-height: 1.4;
      opacity: 0;
      pointer-events: none;
      visibility: hidden;
      box-shadow: 0 0.6rem 1.2rem rgba(0, 0, 0, 0.35);
    }
    .help-icon:hover::after, .help-icon:focus-visible::after {
      opacity: 1;
      visibility: visible;
    }
    .randomness-fields {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
    }
    .randomness-field {
      display: grid;
      gap: 0.55rem;
      padding: 0.75rem;
      border: 1px solid #2b435f;
      border-radius: 0.55rem;
      background: #0d1b2d;
    }
    .randomness-title { display: flex; align-items: center; gap: 0.35rem; color: #edf5ff; font-weight: 700; }
    .randomness-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.55rem; }
    .randomness-inputs label { color: #91a8c1; font-size: 0.78rem; }
    .day-field { display: grid; align-content: start; gap: 0.3rem; }
    .day-picker { display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .day-toggle { min-width: 2.8rem; margin-top: 0; padding: 0.55rem 0.65rem; }
    .day-toggle[aria-pressed="true"] { border-color: #4c9fe8; background: #2479bd; }
    .day-field .form-note { margin-top: 0.15rem; }
    .schedule-list { display: grid; gap: 0.5rem; margin-top: 1rem; }
    .schedule-row { display: flex; justify-content: space-between; gap: 1rem; padding: 0.6rem 0.7rem; border-radius: 0.45rem; background: #0d1b2d; color: #c7d7eb; font-size: 0.82rem; }
    .schedule-row span:last-child { color: #91a8c1; text-align: right; }
    .timer-card { margin-top: 1rem; }
    .timer-list { display: grid; gap: 0.5rem; margin-top: 1rem; }
    .timer-row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 0.65rem 0.7rem; border-radius: 0.45rem; background: #0d1b2d; color: #c7d7eb; font-size: 0.82rem; }
    .timer-details { display: grid; gap: 0.15rem; }
    .timer-details strong { color: #edf5ff; }
    .timer-details span { color: #91a8c1; }
    .timer-row button { margin-top: 0; white-space: nowrap; }
    .progression-card { margin-top: 1rem; }
    .progression-list { display: grid; gap: 0.65rem; margin-top: 1rem; }
    .progression-row { display: grid; gap: 0.45rem; padding: 0.7rem; border-radius: 0.45rem; background: #0d1b2d; color: #c7d7eb; font-size: 0.82rem; }
    .progression-row-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
    .progression-row strong { color: #edf5ff; }
    .progression-row button { margin-top: 0; white-space: nowrap; }
    .progression-meter { height: 0.45rem; overflow: hidden; border-radius: 999px; background: #1a2d46; }
    .progression-meter span { display: block; height: 100%; border-radius: inherit; background: #70b7ff; }
    .quota-card { margin-top: 1rem; }
    .quota-list { display: grid; gap: 0.65rem; margin-top: 1rem; }
    .quota-row { display: grid; gap: 0.45rem; padding: 0.75rem; border: 1px solid #2b435f; border-radius: 0.55rem; background: #0d1b2d; }
    .quota-row-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
    .quota-row strong { color: #edf5ff; }
    .quota-row span { color: #91a8c1; font-size: 0.82rem; }
    .quota-row.reached { border-color: #a9565d; }
    .quota-row.reached .quota-status { color: #ffb0b0; }
    .quota-meter { height: 0.45rem; overflow: hidden; border-radius: 999px; background: #1a2d46; }
    .quota-meter span { display: block; height: 100%; border-radius: inherit; background: #70d7a9; }
    .quota-row.reached .quota-meter span { background: #e97878; }
    .quota-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
    .quota-actions button { margin-top: 0.2rem; }
    .quota-domain-picker { min-height: 8rem; }
    .quota-check { display: flex; align-items: center; gap: 0.5rem; }
    .quota-check input { width: auto; }
    .quota-reset-fields { display: contents; }
    .fields {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    }
    label { display: grid; gap: 0.3rem; color: #c7d7eb; font-size: 0.88rem; }
    input {
      width: 100%;
      max-width: 100%;
      padding: 0.58rem 0.65rem;
      border: 1px solid #3a526e;
      border-radius: 0.45rem;
      background: #0b1727;
      color: #edf5ff;
      font: inherit;
    }
    .token-card { margin-top: 1rem; }
    .token-card label { max-width: 34rem; }
    .token-controls { display: flex; align-items: end; gap: 0.75rem; }
    .token-controls label { flex: 1; }
    .token-controls button { margin-top: 0; white-space: nowrap; }
    .token-check-status { min-height: 1.25rem; margin: 0.65rem 0 0; color: #91a8c1; }
    .token-check-status.success { color: #8de2bc; }
    .token-check-status.error { color: #ffb0b0; }
    .data-grid { margin-top: 1rem; }
    @media (max-width: 700px) {
      body { padding-top: 1rem; }
      .page-header, .action-bar { display: block; }
      .badge { display: inline-block; margin-top: 1rem; }
      .action-buttons { flex-wrap: wrap; }
      .action-buttons button { margin-top: 0.9rem; }
      .content-grid, .data-grid { grid-template-columns: 1fr; }
      .policy-grid { grid-template-columns: 1fr; }
      .uplink-grid { grid-template-columns: 1fr; }
      .connected-row { grid-template-columns: auto minmax(0, 1fr) auto; }
      .connected-controls { grid-column: 1 / -1; }
      .connected-controls select { flex: 1; }
      .observation-actions { margin-top: 0.75rem; }
      .observation-actions button { width: 100%; }
      .observation-device-heading { display: block; }
      .randomness-inputs { grid-template-columns: 1fr; }
      .timer-row { display: block; }
      .timer-row button { width: 100%; margin-top: 0.7rem; }
      .progression-row-heading { display: block; }
      .progression-row button { width: 100%; margin-top: 0.7rem; }
      .quota-row-heading { display: block; }
      .quota-actions button { flex: 1; }
      .token-controls { align-items: stretch; flex-direction: column; }
      .traffic-controls { grid-template-columns: 1fr; }
      .traffic-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (prefers-reduced-motion: reduce) {
      button { transition: none; }
      .toast.working .toast-icon, button[aria-busy="true"]::after { animation: none; }
    }
  </style>
</head>
<body>
  <div id="toast" class="toast" role="status" aria-live="polite" aria-atomic="true" hidden>
    <span id="toast-icon" class="toast-icon" aria-hidden="true"></span>
    <span id="toast-message"></span>
    <button id="toast-dismiss" type="button" aria-label="Dismiss message">Dismiss</button>
  </div>
  <header class="page-header">
    <div>
      <p class="eyebrow">NETWORK TEST LAB</p>
      <h1>JitterSim</h1>
      <p class="subtitle">Shape the test network then observe the result from a connected device.</p>
    </div>
    <span id="mode-badge" class="badge pending">Checking mode...</span>
  </header>
  <p id="mode-notice" class="notice" role="status">Checking control mode...</p>
  <section class="card action-bar" aria-label="Network actions">
    <div class="action-copy">
      <h2>Network controls</h2>
      <p>Apply the saved configuration or return the test network to its normal state.</p>
      <p id="runtime-summary" class="runtime-summary unknown">Checking runtime state...</p>
    </div>
    <div class="action-buttons">
      <button id="apply-button" class="primary" type="button">Apply current configuration</button>
      <button id="reset-button" class="danger" type="button">Reset network</button>
    </div>
    <p id="message" class="action-message" role="status" aria-live="polite">Ready.</p>
  </section>
  <section class="card connected-card">
    <div class="section-heading">
      <div>
        <h2>Connected clients</h2>
        <p>Clients currently visible on the JitterSim test network</p>
      </div>
      <span id="connected-count" class="count-badge">Checking...</span>
    </div>
    <div id="connected-devices" class="connected-list" aria-live="polite">Loading...</div>
  </section>
  <section class="card traffic-card" aria-labelledby="traffic-title">
    <div class="section-heading">
      <div>
        <h2 id="traffic-title">Live traffic</h2>
        <p>Recent traffic rates for assigned test devices</p>
      </div>
      <span id="traffic-window" class="count-badge">Last 60 seconds</span>
    </div>
    <div class="traffic-controls">
      <label>Device
        <select id="traffic-device">
          <option value="all">All assigned devices</option>
        </select>
      </label>
      <p id="traffic-status" class="traffic-status" role="status" aria-live="polite">Check the admin token to start live traffic sampling.</p>
    </div>
    <div class="traffic-metrics">
      <div class="traffic-metric"><span>Upload rate</span><strong id="traffic-upload-rate">—</strong></div>
      <div class="traffic-metric"><span>Download rate</span><strong id="traffic-download-rate">—</strong></div>
      <div class="traffic-metric"><span>Upload packet rate</span><strong id="traffic-upload-packets">—</strong></div>
      <div class="traffic-metric"><span>Download packet rate</span><strong id="traffic-download-packets">—</strong></div>
    </div>
    <div class="traffic-chart-wrap">
      <canvas id="traffic-chart" width="900" height="320" role="img" aria-label="Live upload and download traffic rates over the last 60 seconds">Live traffic chart. Check the admin token to begin sampling.</canvas>
      <div class="traffic-legend" aria-hidden="true">
        <span><i class="traffic-key"></i>Upload</span>
        <span><i class="traffic-key download"></i>Download</span>
      </div>
    </div>
    <p class="traffic-note">These rates count traffic before JitterSim adds delay or drops packets. They show what reaches JitterSim, not the speed a device successfully receives. Only assigned devices are included. Check the admin token to start; sampling pauses when this tab is hidden.</p>
  </section>
  <section class="card uplink-card">
    <div class="section-heading">
      <div>
        <h2>Upstream router connection</h2>
        <p>What the Pi can see through Ethernet</p>
      </div>
      <span id="uplink-state" class="count-badge">Checking...</span>
    </div>
    <div id="uplink-details" class="uplink-grid" aria-live="polite">Loading...</div>
    <p id="uplink-note" class="policy-note">The router controls use the TG3442DE local web interface. The router admin password is used for one request and is not saved.</p>
    <a id="router-settings-link" class="button-link" href="http://192.168.0.1" target="_blank" rel="noreferrer">Open router Wi-Fi settings</a>
    <div class="router-controls">
      <label>Router admin password
        <input id="router-admin-password" type="password" autocomplete="off" placeholder="Password for 192.168.0.1">
      </label>
      <div class="action-buttons">
        <button id="sync-router-ap" class="primary" type="button">Copy router Wi-Fi to Pi</button>
        <button id="disable-router-wifi" class="danger" type="button">Turn off router Wi-Fi</button>
        <button id="enable-router-wifi" type="button">Turn on router Wi-Fi</button>
      </div>
      <p id="router-action-message" class="policy-note" role="status" aria-live="polite">Keep the Pi connected to the router by Ethernet before changing the router Wi-Fi.</p>
    </div>
  </section>
  <section class="card observations-card">
    <div class="section-heading">
      <div>
        <h2>Traffic discovery</h2>
        <p>Review DNS names observed from devices on the test network</p>
      </div>
      <div class="observation-actions">
        <span id="observation-count" class="count-badge">No scan yet</span>
        <button id="scan-observations" class="primary" type="button">Scan now</button>
      </div>
    </div>
    <p id="observation-note" class="observation-note">Scanning is on demand. Results are local aggregates, not exact page visits.</p>
    <div id="observations" class="observation-list" aria-live="polite">No observations yet.</div>
  </section>
  <section class="card token-card">
    <h2>Control token</h2>
    <div class="token-controls">
      <label>Admin token (kept in this page only)
        <input id="admin-token" type="password" autocomplete="off">
      </label>
      <button id="check-admin-token" type="button">Check token</button>
    </div>
    <p id="token-check-status" class="token-check-status" role="status" aria-live="polite">Enter the token and choose Check token to load diagnostics.</p>
  </section>
  <div class="content-grid">
    <section class="card">
      <h2>Save profile</h2>
      <form id="profile-form">
        <div class="fields">
          <label>Profile ID
            <input id="profile-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}">
          </label>
          <label>Name <input id="profile-name" required></label>
        </div>
        <h3>Upload</h3>
        <div class="fields">
          <label>Delay (ms) <input id="upload-delay" type="number" min="0" value="0" required></label>
          <label>Jitter (ms)
            <input id="upload-jitter" type="number" min="0" value="0" required>
          </label>
          <label>Loss (%)
            <input id="upload-loss" type="number" min="0" max="100" value="0" required>
          </label>
          <label>Bandwidth (kbit/s, optional)
            <input id="upload-bandwidth" type="number" min="1">
          </label>
        </div>
        <h3>Download</h3>
        <div class="fields">
          <label>Delay (ms)
            <input id="download-delay" type="number" min="0" value="0" required>
          </label>
          <label>Jitter (ms)
            <input id="download-jitter" type="number" min="0" value="0" required>
          </label>
          <label>Loss (%)
            <input id="download-loss" type="number" min="0" max="100" value="0" required>
          </label>
          <label>Bandwidth (kbit/s, optional)
            <input id="download-bandwidth" type="number" min="1">
          </label>
        </div>
        <details>
          <summary>Randomness, outages and website access</summary>
          <p class="form-note">Values are rerolled at each interval. Hover a question mark for an explanation. A 200 ms value with 30% variation can be rerolled anywhere from 140 to 260 ms. Enabled chance controls whether that feature is active for the interval.</p>
          <div class="fields">
            <label><span class="field-label">Reroll every (seconds)<span class="help-icon" tabindex="0" role="img" aria-label="Reroll interval help" data-help="The base time between new random values. Set this to 0 to keep the saved values fixed.">?</span></span>
              <input id="profile-reroll" type="number" min="0" value="0">
            </label>
            <label><span class="field-label">Interval variation (%)<span class="help-icon" tabindex="0" role="img" aria-label="Interval variation help" data-help="The reroll interval can move this percentage below or above the base interval. For example, 20% of 5 minutes means 4 to 6 minutes.">?</span></span>
              <input id="profile-reroll-variation" type="number" min="0" max="100" value="0">
            </label>
            <label><span class="field-label">Outage chance (%)<span class="help-icon" tabindex="0" role="img" aria-label="Outage chance help" data-help="The chance that a device is disconnected when the outage check runs.">?</span></span>
              <input id="profile-outage-probability" type="number" min="0" max="100" value="0">
            </label>
            <label><span class="field-label">Outage length (seconds)<span class="help-icon" tabindex="0" role="img" aria-label="Outage length help" data-help="How long a selected outage lasts before the device is allowed to use the network again.">?</span></span>
              <input id="profile-outage-duration" type="number" min="0" value="0">
            </label>
            <label><span class="field-label">Outage check interval (seconds)<span class="help-icon" tabindex="0" role="img" aria-label="Outage interval help" data-help="How often JitterSim checks whether to start a new outage. Set this to 0 to use the reroll interval.">?</span></span>
              <input id="profile-outage-interval" type="number" min="0" value="0">
            </label>
            <label>Random seed (optional)
              <input id="profile-seed" type="number" min="0">
            </label>
            <label>Website mode
              <select id="profile-website-mode">
                <option value="unrestricted">Unrestricted</option>
                <option value="allow_only">Allow only listed domains</option>
              </select>
            </label>
            <label>Profile domains (comma separated)
              <input id="profile-website-domains" placeholder="linkedin.com, chat.openai.com">
            </label>
            <label><span class="field-label">Website entry chance (%)<span class="help-icon" tabindex="0" role="img" aria-label="Website chance help" data-help="At each profile reroll, this chance controls whether each listed profile domain stays allowed for the next interval.">?</span></span>
              <input id="profile-website-enabled" type="number" min="0" max="100" value="100">
            </label>
          </div>
          <h3>Upload variation</h3>
          <p class="form-note">Each card controls one feature. Value variation changes the base value. Enabled chance decides whether the feature is used after a reroll.</p>
          <div class="fields">
            <label>Reorder (%) <input id="upload-reorder" type="number" min="0" max="100" value="0"></label>
            <label>Duplicate (%) <input id="upload-duplicate" type="number" min="0" max="100" value="0"></label>
            <label>Corrupt (%) <input id="upload-corrupt" type="number" min="0" max="100" value="0"></label>
          </div>
          <div class="randomness-fields">
            <div class="randomness-field">
              <div class="randomness-title">Delay <span class="help-icon" tabindex="0" role="img" aria-label="Delay randomness help" data-help="Variation changes the base delay up or down. Enabled chance controls whether delay is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-delay-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-delay-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Jitter <span class="help-icon" tabindex="0" role="img" aria-label="Jitter randomness help" data-help="Variation changes the base jitter up or down. Enabled chance controls whether jitter is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-jitter-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-jitter-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Loss <span class="help-icon" tabindex="0" role="img" aria-label="Loss randomness help" data-help="Variation changes the base packet-loss percentage up or down. Enabled chance controls whether loss is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-loss-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-loss-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Bandwidth <span class="help-icon" tabindex="0" role="img" aria-label="Bandwidth randomness help" data-help="Variation changes the base speed limit up or down. Enabled chance controls whether the speed limit is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-bandwidth-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-bandwidth-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Reorder <span class="help-icon" tabindex="0" role="img" aria-label="Reorder randomness help" data-help="Variation changes the base packet-reorder percentage up or down. Enabled chance controls whether reorder is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-reorder-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-reorder-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Duplicate <span class="help-icon" tabindex="0" role="img" aria-label="Duplicate randomness help" data-help="Variation changes the base packet-duplicate percentage up or down. Enabled chance controls whether duplicate is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-duplicate-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-duplicate-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Corrupt <span class="help-icon" tabindex="0" role="img" aria-label="Corrupt randomness help" data-help="Variation changes the base packet-corruption percentage up or down. Enabled chance controls whether corruption is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="upload-corrupt-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="upload-corrupt-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
          </div>
          <h3>Download variation</h3>
          <div class="fields">
            <label>Reorder (%) <input id="download-reorder" type="number" min="0" max="100" value="0"></label>
            <label>Duplicate (%) <input id="download-duplicate" type="number" min="0" max="100" value="0"></label>
            <label>Corrupt (%) <input id="download-corrupt" type="number" min="0" max="100" value="0"></label>
          </div>
          <div class="randomness-fields">
            <div class="randomness-field">
              <div class="randomness-title">Delay <span class="help-icon" tabindex="0" role="img" aria-label="Delay randomness help" data-help="Variation changes the base delay up or down. Enabled chance controls whether delay is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-delay-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-delay-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Jitter <span class="help-icon" tabindex="0" role="img" aria-label="Jitter randomness help" data-help="Variation changes the base jitter up or down. Enabled chance controls whether jitter is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-jitter-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-jitter-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Loss <span class="help-icon" tabindex="0" role="img" aria-label="Loss randomness help" data-help="Variation changes the base packet-loss percentage up or down. Enabled chance controls whether loss is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-loss-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-loss-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Bandwidth <span class="help-icon" tabindex="0" role="img" aria-label="Bandwidth randomness help" data-help="Variation changes the base speed limit up or down. Enabled chance controls whether the speed limit is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-bandwidth-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-bandwidth-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Reorder <span class="help-icon" tabindex="0" role="img" aria-label="Reorder randomness help" data-help="Variation changes the base packet-reorder percentage up or down. Enabled chance controls whether reorder is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-reorder-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-reorder-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Duplicate <span class="help-icon" tabindex="0" role="img" aria-label="Duplicate randomness help" data-help="Variation changes the base packet-duplicate percentage up or down. Enabled chance controls whether duplicate is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-duplicate-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-duplicate-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
            <div class="randomness-field">
              <div class="randomness-title">Corrupt <span class="help-icon" tabindex="0" role="img" aria-label="Corrupt randomness help" data-help="Variation changes the base packet-corruption percentage up or down. Enabled chance controls whether corruption is active for the interval.">?</span></div>
              <div class="randomness-inputs">
                <label>Value variation (%)<input id="download-corrupt-random-variation" type="number" min="0" max="100" value="0"></label>
                <label>Enabled chance (%)<input id="download-corrupt-random-enabled" type="number" min="0" max="100" value="100"></label>
              </div>
            </div>
          </div>
        </details>
        <button type="submit">Save profile</button>
      </form>
    </section>
    <section class="card">
      <h2>Assign a device</h2>
      <form id="device-form">
        <div class="fields">
          <label>Device ID <input id="device-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}"></label>
          <label>Name <input id="device-name" required></label>
          <label>Reserved IPv4 address
            <input id="device-ip" required placeholder="192.168.50.10">
          </label>
          <label>Profile ID <input id="device-profile" required></label>
          <label>MAC address (optional)
            <input id="device-mac" placeholder="aa:bb:cc:dd:ee:ff">
          </label>
        </div>
        <button type="submit">Save device</button>
      </form>
    </section>
    <section class="card">
      <h2>Group devices</h2>
      <p class="form-note">Choose several assigned devices to give them one profile override or one shared data limit. A device can belong to one group.</p>
      <form id="group-form">
        <div class="fields">
          <label>Group ID <input id="group-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="family"></label>
          <label>Group name <input id="group-name" required placeholder="Family devices"></label>
          <label>Profile override
            <select id="group-profile"><option value="">Use each device profile</option></select>
          </label>
          <label>Devices
            <select id="group-devices" multiple required size="5"></select>
          </label>
        </div>
        <button type="submit">Save group</button>
      </form>
      <div id="group-list" class="quota-list" aria-live="polite">No groups saved.</div>
    </section>
  </div>
  <div class="content-grid">
    <section class="card">
      <h2>Network access rules</h2>
      <form id="policy-form">
        <div class="policy-grid">
          <div>
            <h3>MAC filtering</h3>
            <div class="fields">
              <label>Mode
                <select id="mac-mode">
                  <option value="disabled">Disabled</option>
                  <option value="allowlist">Allow only these MAC addresses</option>
                  <option value="denylist">Block these MAC addresses</option>
                </select>
              </label>
              <label>Allowlist (comma separated)
                <textarea id="mac-allowlist" placeholder="aa:bb:cc:dd:ee:ff"></textarea>
              </label>
              <label>Denylist (comma separated)
                <textarea id="mac-denylist" placeholder="11:22:33:44:55:66"></textarea>
              </label>
            </div>
          </div>
          <div>
            <h3>Website filtering</h3>
            <div class="fields">
              <label>Global mode
                <select id="website-mode">
                  <option value="unrestricted">Unrestricted</option>
                  <option value="allow_only">Allow only these domains</option>
                </select>
              </label>
              <label>Global allowed domains (comma separated)
                <textarea id="website-allowlist" placeholder="example.com"></textarea>
              </label>
              <label>Global blocked domains (comma separated)
                <textarea id="website-blocklist" placeholder="ads.example.com"></textarea>
              </label>
            </div>
          </div>
        </div>
        <p class="policy-note">Website rules use resolved IPv4 destinations and are best effort. VPNs, encrypted DNS and direct IP connections can bypass them.</p>
        <button id="save-policies" type="submit">Save access rules</button>
      </form>
    </section>
    <section class="card">
      <h2>Schedule a profile</h2>
      <form id="schedule-form">
        <div class="fields">
          <label>Schedule ID <input id="schedule-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}"></label>
          <label>Device
            <select id="schedule-device" required></select>
          </label>
          <label>Profile
            <select id="schedule-profile" required></select>
          </label>
          <div class="day-field">
            <span class="field-label">Days of the week</span>
            <div id="schedule-day-picker" class="day-picker" role="group" aria-label="Days of the week">
              <button class="day-toggle" type="button" data-day="0" aria-pressed="true">Mon</button>
              <button class="day-toggle" type="button" data-day="1" aria-pressed="true">Tue</button>
              <button class="day-toggle" type="button" data-day="2" aria-pressed="true">Wed</button>
              <button class="day-toggle" type="button" data-day="3" aria-pressed="true">Thu</button>
              <button class="day-toggle" type="button" data-day="4" aria-pressed="true">Fri</button>
              <button class="day-toggle" type="button" data-day="5" aria-pressed="false">Sat</button>
              <button class="day-toggle" type="button" data-day="6" aria-pressed="false">Sun</button>
            </div>
            <input id="schedule-days" type="hidden" value="0,1,2,3,4">
            <p class="form-note">Click a day to select or deselect it. Choose at least one.</p>
          </div>
          <label>Start <input id="schedule-start" type="time" value="09:00" required></label>
          <label>End <input id="schedule-end" type="time" value="17:00" required></label>
        </div>
        <button type="submit">Save schedule</button>
      </form>
      <div id="schedule-list" class="schedule-list" aria-live="polite">No schedules yet.</div>
    </section>
  </div>
  <section class="card timer-card">
    <div class="section-heading">
      <div>
        <h2>Run a profile timer</h2>
        <p>Temporarily use a profile for one device</p>
      </div>
      <span id="timer-count" class="count-badge">No active timers</span>
    </div>
    <form id="timer-form">
      <div class="fields">
        <label>Device
          <select id="timer-device" required></select>
        </label>
        <label>Profile
          <select id="timer-profile" required></select>
        </label>
        <label>Duration (minutes)
          <input id="timer-duration" type="number" min="1" max="10080" value="10" required>
        </label>
      </div>
      <p class="form-note">The timer starts when saved. It temporarily overrides schedules and the device’s normal profile. The selected profile’s rerolls, outages, website rules and other settings run during the timer then the device returns to its normal profile.</p>
      <button class="primary" type="submit">Start timer</button>
    </form>
    <div id="timer-list" class="timer-list" aria-live="polite">No active timers.</div>
  </section>
  <section class="card progression-card">
    <div class="section-heading">
      <div>
        <h2>Progression mode</h2>
        <p>Move gradually from one profile to another</p>
      </div>
      <span id="progression-count" class="count-badge">No active progressions</span>
    </div>
    <form id="progression-form">
      <div class="fields">
        <label>Device
          <select id="progression-device" required></select>
        </label>
        <label>Start profile
          <select id="progression-start-profile" required></select>
        </label>
        <label>End profile
          <select id="progression-end-profile" required></select>
        </label>
        <label>Mode
          <select id="progression-mode">
            <option value="time">Over time</option>
            <option value="traffic">After DNS queries</option>
            <option value="bytes">After device data</option>
          </select>
        </label>
        <label>Duration limit (minutes)
          <input id="progression-duration" type="number" min="1" max="10080" value="300" required>
        </label>
        <label id="progression-traffic-field" hidden>DNS queries to reach the end
          <input id="progression-traffic-target" type="number" min="1" max="1000000000" value="1000">
        </label>
        <label id="progression-bytes-field" hidden>Device data to reach the end (MB)
          <input id="progression-bytes-target" type="number" min="1" max="10000000" value="1000">
        </label>
      </div>
      <label>Domains to block gradually (optional, comma separated)
        <textarea id="progression-domains" placeholder="youtube.com, example.com"></textarea>
      </label>
      <p class="form-note">The start and end profiles are interpolated gradually. Existing randomness and outage settings continue to run. DNS mode counts observed queries. Device-data mode counts IPv4 upload and download bytes, so a long stream can advance it even when there are few new DNS queries. The device returns to its normal schedule or profile when the duration ends.</p>
      <button class="primary" type="submit">Start progression</button>
    </form>
    <div id="progression-list" class="progression-list" aria-live="polite">No active progressions.</div>
  </section>
  <section class="card quota-card">
    <div class="section-heading">
      <div>
        <h2>Device data limit</h2>
        <p>Keep normal internet access until a device reaches its byte limit</p>
      </div>
      <span id="data-limit-count" class="count-badge">No limits</span>
    </div>
    <form id="data-limit-form">
      <div class="fields">
        <label>Device
          <select id="data-limit-device" required></select>
        </label>
        <label>Fallback profile
          <select id="data-limit-profile" required></select>
        </label>
        <label>Limit
          <input id="data-limit-amount" type="number" min="1" step="1" value="1" required>
        </label>
        <label>Unit
          <select id="data-limit-unit">
            <option value="1048576">MB</option>
            <option value="1073741824">GB</option>
          </select>
        </label>
        <label>Reset
          <select id="data-limit-reset-mode">
            <option value="manual">Manual</option>
            <option value="interval">After an interval</option>
            <option value="daily">At fixed times each day</option>
          </select>
        </label>
        <label id="data-limit-interval-field">Interval (minutes)
          <input id="data-limit-interval" type="number" min="1" max="525600" value="60">
        </label>
        <label id="data-limit-times-field" hidden>Reset times (HH:MM, comma separated)
          <input id="data-limit-times" placeholder="08:00, 20:00">
        </label>
        <label class="quota-check"><input id="data-limit-enabled" type="checkbox" checked> Enabled</label>
      </div>
      <p class="form-note">Editing a saved limit keeps its current usage. The fallback profile starts when the device total reaches the limit.</p>
      <button class="primary" type="submit">Save device limit</button>
    </form>
    <div id="data-limit-list" class="quota-list" aria-live="polite">Enter the admin token to view limits.</div>
  </section>
  <section class="card quota-card">
    <div class="section-heading">
      <div>
        <h2>Shared group data limit</h2>
        <p>Count the combined traffic of every device in a group</p>
      </div>
      <span id="group-data-limit-count" class="count-badge">No group limits</span>
    </div>
    <form id="group-data-limit-form">
      <div class="fields">
        <label>Group
          <select id="group-data-limit-group" required></select>
        </label>
        <label>Fallback profile
          <select id="group-data-limit-profile" required></select>
        </label>
        <label>Limit
          <input id="group-data-limit-amount" type="number" min="1" step="1" value="1" required>
        </label>
        <label>Unit
          <select id="group-data-limit-unit">
            <option value="1048576">MB</option>
            <option value="1073741824">GB</option>
          </select>
        </label>
        <label>Reset
          <select id="group-data-limit-reset-mode">
            <option value="manual">Manual</option>
            <option value="interval">After an interval</option>
            <option value="daily">At fixed times each day</option>
          </select>
        </label>
        <label id="group-data-limit-interval-field">Interval (minutes)
          <input id="group-data-limit-interval" type="number" min="1" max="525600" value="60">
        </label>
        <label id="group-data-limit-times-field" hidden>Reset times (HH:MM, comma separated)
          <input id="group-data-limit-times" placeholder="08:00, 20:00">
        </label>
        <label class="quota-check"><input id="group-data-limit-enabled" type="checkbox" checked> Enabled</label>
      </div>
      <p class="form-note">The shared limit adds the byte counters for all group members. A per-device limit takes priority if the same device has both.</p>
      <button class="primary" type="submit">Save group limit</button>
    </form>
    <div id="group-data-limit-list" class="quota-list" aria-live="polite">Enter the admin token to view group limits.</div>
  </section>
  <section class="card quota-card">
    <div class="section-heading">
      <div>
        <h2>Service data limit</h2>
        <p>Limit a service made up of several observed DNS domains</p>
      </div>
      <span id="service-quota-count" class="count-badge">No service limits</span>
    </div>
    <form id="service-quota-form">
      <div class="fields">
        <label>Service ID
          <input id="service-quota-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="alice-netflix">
        </label>
        <label>Service name
          <input id="service-quota-name" required placeholder="Netflix">
        </label>
        <label>Device
          <select id="service-quota-device" required></select>
        </label>
        <label>Fallback profile
          <select id="service-quota-profile" required></select>
        </label>
        <label>Limit
          <input id="service-quota-amount" type="number" min="1" step="1" value="1" required>
        </label>
        <label>Unit
          <select id="service-quota-unit">
            <option value="1048576">MB</option>
            <option value="1073741824">GB</option>
          </select>
        </label>
        <label><span class="field-label">Quota weight (×)<span class="help-icon" tabindex="0" role="img" aria-label="Quota weight help" data-help="A weight of 1.2 makes each matching byte count as 1.2 bytes. A weight of 0.8 makes it count as 0.8 bytes. The allowed range is 0.1× to 10×.">?</span></span>
          <input id="service-quota-weight" type="number" min="0.1" max="10" step="0.1" value="1" required>
        </label>
        <label>Reset
          <select id="service-quota-reset-mode">
            <option value="manual">Manual</option>
            <option value="interval">After an interval</option>
            <option value="daily">At fixed times each day</option>
          </select>
        </label>
        <label id="service-quota-interval-field">Interval (minutes)
          <input id="service-quota-interval" type="number" min="1" max="525600" value="60">
        </label>
        <label id="service-quota-times-field" hidden>Reset times (HH:MM, comma separated)
          <input id="service-quota-times" placeholder="08:00, 20:00">
        </label>
        <label class="quota-check"><input id="service-quota-enabled" type="checkbox" checked> Enabled</label>
      </div>
      <label>Observed domains for this service
        <select id="service-quota-domains" class="quota-domain-picker" multiple required></select>
      </label>
      <p class="form-note">Only domains observed from the selected device appear here. Select all names used by the service. Matching traffic is counted together at the service level then multiplied by the quota weight.</p>
      <button class="primary" type="submit">Save service limit</button>
    </form>
    <div id="service-quota-list" class="quota-list" aria-live="polite">Enter the admin token to view service limits.</div>
  </section>
  <section class="card quota-card">
    <div class="section-heading">
      <div>
        <h2>Service time limit</h2>
        <p>Allow a service a set amount of active traffic time inside a timer window</p>
      </div>
      <span id="time-quota-count" class="count-badge">No time limits</span>
    </div>
    <form id="time-quota-form">
      <div class="fields">
        <label>Service ID
          <input id="time-quota-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="alice-netflix-time">
        </label>
        <label>Service name
          <input id="time-quota-name" required placeholder="Netflix time">
        </label>
        <label>Device
          <select id="time-quota-device" required></select>
        </label>
        <label>Fallback profile
          <select id="time-quota-profile" required></select>
        </label>
        <label>Allowed active time
          <input id="time-quota-amount" type="number" min="1" step="1" value="5" required>
        </label>
        <label>Time unit
          <select id="time-quota-unit">
            <option value="3600">Hours</option>
            <option value="60">Minutes</option>
          </select>
        </label>
        <label><span class="field-label">Timer window (minutes)<span class="help-icon" tabindex="0" role="img" aria-label="Timer window help" data-help="The quota can count service activity only while this timer is running. Starting the timer clears this quota's current usage and starts a fresh window.">?</span></span>
          <input id="time-quota-window" type="number" min="1" max="10080" step="1" value="720" required>
        </label>
        <label>Reset
          <select id="time-quota-reset-mode">
            <option value="manual">Manual</option>
            <option value="interval">After an interval</option>
            <option value="daily">At fixed times each day</option>
          </select>
        </label>
        <label id="time-quota-interval-field">Interval (minutes)
          <input id="time-quota-interval" type="number" min="1" max="525600" value="60">
        </label>
        <label id="time-quota-times-field" hidden>Reset times (HH:MM, comma separated)
          <input id="time-quota-times" placeholder="08:00, 20:00">
        </label>
        <label class="quota-check"><input id="time-quota-enabled" type="checkbox" checked> Enabled</label>
      </div>
      <label>Observed domains for this service
        <select id="time-quota-domains" class="quota-domain-picker" multiple required></select>
      </label>
      <p class="form-note">Active time is counted when matching traffic is seen. A long video stream therefore keeps using the allowance even when there are no new clicks. Save the limit then start its timer from the saved row.</p>
      <button class="primary" type="submit">Save time limit</button>
    </form>
    <div id="time-quota-list" class="quota-list" aria-live="polite">Enter the admin token to view time limits.</div>
  </section>
  <section class="card">
    <h2>Manual Pi access-point fallback</h2>
    <form id="access-point-form">
      <div class="fields">
        <label>Test network name (SSID)
          <input id="access-point-ssid" value="JitterSim-Test" maxlength="32" required>
        </label>
        <label>Test network password
          <input id="access-point-password" type="password" minlength="8" maxlength="63" autocomplete="new-password" required>
        </label>
      </div>
      <p class="policy-note">Use this if router import is unavailable. These values configure the Pi only. The password is sent to the helper and is not saved in the dashboard database.</p>
      <button type="submit">Use these values on the Pi</button>
    </form>
  </section>
  <div class="data-grid">
    <section class="card">
      <div class="section-heading"><h2>Profiles</h2><p>Saved network behavior</p></div>
      <pre id="profiles">Loading...</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Devices</h2><p>Assigned test clients</p></div>
      <pre id="devices">Loading...</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Compiled plan</h2><p>Current traffic rules</p></div>
      <pre id="plan">Loading...</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Runtime state</h2><p>Last helper result</p></div>
      <pre id="state">Loading...</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Access rules</h2><p>Saved MAC and website policy</p></div>
      <pre id="policies">Loading...</pre>
    </section>
    <section class="card">
      <div class="section-heading">
        <div><h2>Diagnostics</h2><p>Runtime state and available traffic counters</p></div>
        <button id="refresh-diagnostics" type="button">Refresh</button>
      </div>
      <pre id="diagnostics">Enter the admin token to inspect diagnostics.</pre>
    </section>
    <section class="card">
      <div class="section-heading">
        <div><h2>Backup and action history</h2><p>Recent admin actions without request contents</p></div>
        <button id="download-backup" type="button">Download backup</button>
      </div>
      <pre id="action-history">Enter the admin token to view recent actions.</pre>
    </section>
  </div>
  <script>
    const element = (id) => document.getElementById(id);
    let pendingFormSubmitButton = null;

    function optionalNumber(id) {
      const value = element(id).value.trim();
      return value === "" ? null : Number(value);
    }

    function commaList(id) {
      return element(id).value.split(",").map((value) => value.trim()).filter(Boolean);
    }

    function featureRandomness(prefix, feature) {
      const variation = element(prefix + "-" + feature + "-random-variation");
      const enabled = element(prefix + "-" + feature + "-random-enabled");
      return variation && enabled ? {
        variation_percent: Number(variation.value),
        enabled_probability: Number(enabled.value)
      } : { variation_percent: 0, enabled_probability: 100 };
    }

    function impairment(prefix) {
      return {
        delay_ms: Number(element(prefix + "-delay").value),
        jitter_ms: Number(element(prefix + "-jitter").value),
        loss_percent: Number(element(prefix + "-loss").value),
        bandwidth_kbit: optionalNumber(prefix + "-bandwidth"),
        reorder_percent: Number(element(prefix + "-reorder").value),
        duplicate_percent: Number(element(prefix + "-duplicate").value),
        corrupt_percent: Number(element(prefix + "-corrupt").value),
        delay_randomness: featureRandomness(prefix, "delay"),
        jitter_randomness: featureRandomness(prefix, "jitter"),
        loss_randomness: featureRandomness(prefix, "loss"),
        bandwidth_randomness: featureRandomness(prefix, "bandwidth"),
        reorder_randomness: featureRandomness(prefix, "reorder"),
        duplicate_randomness: featureRandomness(prefix, "duplicate"),
        corrupt_randomness: featureRandomness(prefix, "corrupt")
      };
    }

    function timeToMinute(id) {
      const parts = element(id).value.split(":").map(Number);
      return parts[0] * 60 + parts[1];
    }

    function selectedScheduleDays() {
      return Array.from(document.querySelectorAll(".day-toggle[aria-pressed='true']"))
        .map((button) => Number(button.dataset.day));
    }

    function syncScheduleDays() {
      const days = selectedScheduleDays();
      element("schedule-days").value = days.join(",");
      return days;
    }

    document.querySelectorAll(".day-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const selected = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(selected));
        syncScheduleDays();
      });
    });
    syncScheduleDays();

    async function request(path, method, body) {
      const headers = {};
      const token = element("admin-token").value.trim();
      if (token) headers.Authorization = "Bearer " + token;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const submitButton = pendingFormSubmitButton;
      pendingFormSubmitButton = null;
      try {
        const response = await fetch(path, {
          method: method,
          headers: headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          cache: "no-store"
        });
        const data = await response.json();
        if (!response.ok) {
          const error = new Error(data.error || response.statusText);
          error.status = response.status;
          throw error;
        }
        return data;
      } finally {
        if (submitButton) setButtonBusy(submitButton, false);
      }
    }

    async function show(endpoint, elementId, key) {
      const data = await request(endpoint, "GET");
      element(elementId).textContent = JSON.stringify(data[key], null, 2);
    }

    let helperConfigured = false;
    let profiles = [];
    let groups = [];
    let observedDomainsByDevice = {};
    let connectedDevicesRequestInFlight = false;
    let observationRequestInFlight = false;
    const TRAFFIC_POLL_INTERVAL_MS = 2000;
    const TRAFFIC_MAX_POINTS = 30;
    const TRAFFIC_WINDOW_MS = 60000;
    let trafficPollTimer = null;
    let trafficRequestInFlight = false;
    let trafficSamplingAllowed = false;
    let previousTrafficSample = null;
    let trafficSamples = [];

    function showMessage(message, kind) {
      const messageElement = element("message");
      messageElement.textContent = message;
      messageElement.className = "action-message " + (kind || "");
      if (kind !== "working" && pendingFormSubmitButton) {
        setButtonBusy(pendingFormSubmitButton, false);
        pendingFormSubmitButton = null;
      }
      if (kind) showToast(message, kind);
    }

    function resetTrafficHistory() {
      previousTrafficSample = null;
      trafficSamples = [];
      ["traffic-upload-rate", "traffic-download-rate", "traffic-upload-packets", "traffic-download-packets"]
        .forEach((id) => { element(id).textContent = "—"; });
      drawTrafficChart();
    }

    function stopTrafficSampling(message) {
      trafficSamplingAllowed = false;
      if (trafficPollTimer !== null) {
        window.clearInterval(trafficPollTimer);
        trafficPollTimer = null;
      }
      resetTrafficHistory();
      element("traffic-status").textContent = message;
    }

    function formatTrafficRate(megabitsPerSecond) {
      if (megabitsPerSecond < 1) {
        return (megabitsPerSecond * 1000).toFixed(1) + " kbit/s";
      }
      return megabitsPerSecond.toFixed(2) + " Mbit/s";
    }

    function formatPacketRate(packetsPerSecond) {
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(packetsPerSecond) + " pkt/s";
    }

    function selectedTrafficTotals(deviceCounters) {
      const selectedDevice = element("traffic-device").value;
      const counters = selectedDevice === "all"
        ? Object.values(deviceCounters)
        : deviceCounters[selectedDevice] ? [deviceCounters[selectedDevice]] : [];
      if (!counters.length) return null;
      return counters.reduce((totals, current) => {
        ["upload_bytes", "download_bytes", "upload_packets", "download_packets"].forEach((field) => {
          totals[field] += current[field];
        });
        return totals;
      }, { upload_bytes: 0, download_bytes: 0, upload_packets: 0, download_packets: 0 });
    }

    function updateTrafficDeviceOptions(devices) {
      const select = element("traffic-device");
      const selected = select.value;
      select.replaceChildren();
      const allOption = document.createElement("option");
      allOption.value = "all";
      allOption.textContent = "All assigned devices";
      select.appendChild(allOption);
      devices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.id;
        option.textContent = device.name + " (" + device.ipv4_address + ")";
        select.appendChild(option);
      });
      select.value = Array.from(select.options).some((option) => option.value === selected)
        ? selected
        : "all";
    }

    function drawTrafficChart() {
      const canvas = element("traffic-chart");
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const pixelRatio = window.devicePixelRatio || 1;
      const width = Math.round(bounds.width * pixelRatio);
      const height = Math.round(bounds.height * pixelRatio);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, bounds.width, bounds.height);
      const plot = { left: 62, right: bounds.width - 14, top: 16, bottom: bounds.height - 34 };
      const plotWidth = plot.right - plot.left;
      const plotHeight = plot.bottom - plot.top;
      const now = performance.now();
      const visibleSamples = trafficSamples.filter((sample) => now - sample.time <= TRAFFIC_WINDOW_MS);
      const maxRate = Math.max(0.1, ...visibleSamples.map((sample) => Math.max(sample.upload, sample.download)));
      const scaleMax = Math.pow(10, Math.ceil(Math.log10(maxRate)));
      context.font = "12px system-ui, sans-serif";
      context.textBaseline = "middle";
      for (let tick = 0; tick <= 4; tick += 1) {
        const y = plot.bottom - plotHeight * tick / 4;
        context.strokeStyle = "#263c56";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(plot.left, y);
        context.lineTo(plot.right, y);
        context.stroke();
        context.fillStyle = "#91a8c1";
        context.textAlign = "right";
        context.fillText(formatTrafficRate(scaleMax * tick / 4), plot.left - 8, y);
      }
      [0, 0.5, 1].forEach((fraction) => {
        const x = plot.left + plotWidth * fraction;
        context.fillStyle = "#91a8c1";
        context.textAlign = fraction === 0 ? "left" : fraction === 1 ? "right" : "center";
        context.textBaseline = "alphabetic";
        context.fillText(fraction === 1 ? "now" : "−" + Math.round((1 - fraction) * TRAFFIC_WINDOW_MS / 1000) + "s", x, bounds.height - 8);
      });
      [
        { field: "upload", color: "#70b7ff" },
        { field: "download", color: "#70d7a9" }
      ].forEach((series) => {
        context.strokeStyle = series.color;
        context.lineWidth = 2.5;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.beginPath();
        let started = false;
        visibleSamples.forEach((sample) => {
          const age = now - sample.time;
          const x = plot.right - plotWidth * age / TRAFFIC_WINDOW_MS;
          const y = plot.bottom - plotHeight * sample[series.field] / scaleMax;
          if (!started) {
            context.moveTo(x, y);
            started = true;
          } else {
            context.lineTo(x, y);
          }
        });
        if (started) context.stroke();
      });
      if (!visibleSamples.length) {
        context.fillStyle = "#91a8c1";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText("Waiting for traffic samples...", plot.left + plotWidth / 2, plot.top + plotHeight / 2);
      }
    }

    async function pollTraffic() {
      if (trafficRequestInFlight || !trafficSamplingAllowed || document.hidden) return;
      trafficRequestInFlight = true;
      try {
        const data = await request("/api/traffic", "GET");
        if (!trafficSamplingAllowed) return;
        if (!data.available) {
          stopTrafficSampling(data.message || "Live traffic counters are unavailable in this mode.");
          return;
        }
        const totals = selectedTrafficTotals(data.devices || {});
        if (!totals) {
          resetTrafficHistory();
          element("traffic-status").textContent = "No counters yet. Assign a profile and apply the configuration.";
          return;
        }
        const sampledAt = performance.now();
        if (previousTrafficSample) {
          const elapsedSeconds = (sampledAt - previousTrafficSample.time) / 1000;
          const fields = Object.keys(totals);
          const countersReset = fields.some((field) => totals[field] < previousTrafficSample.totals[field]);
          if (countersReset || elapsedSeconds <= 0) {
            resetTrafficHistory();
            element("traffic-status").textContent = "Counters changed. Starting a fresh graph...";
          } else {
            const upload = Math.max(0, totals.upload_bytes - previousTrafficSample.totals.upload_bytes) * 8 / elapsedSeconds / 1000000;
            const download = Math.max(0, totals.download_bytes - previousTrafficSample.totals.download_bytes) * 8 / elapsedSeconds / 1000000;
            const uploadPackets = Math.max(0, totals.upload_packets - previousTrafficSample.totals.upload_packets) / elapsedSeconds;
            const downloadPackets = Math.max(0, totals.download_packets - previousTrafficSample.totals.download_packets) / elapsedSeconds;
            trafficSamples.push({ time: sampledAt, upload: upload, download: download });
            trafficSamples = trafficSamples
              .filter((sample) => sampledAt - sample.time <= TRAFFIC_WINDOW_MS)
              .slice(-TRAFFIC_MAX_POINTS);
            element("traffic-upload-rate").textContent = formatTrafficRate(upload);
            element("traffic-download-rate").textContent = formatTrafficRate(download);
            element("traffic-upload-packets").textContent = formatPacketRate(uploadPackets);
            element("traffic-download-packets").textContent = formatPacketRate(downloadPackets);
            element("traffic-status").textContent = "Sampling live traffic every 2 seconds.";
          }
        } else {
          element("traffic-status").textContent = "Collecting the first sample...";
        }
        previousTrafficSample = { time: sampledAt, totals: totals };
        drawTrafficChart();
      } catch (error) {
        if (!trafficSamplingAllowed) return;
        resetTrafficHistory();
        if (error.status === 401) {
          stopTrafficSampling("The admin token was rejected. Check it again to restart live traffic sampling.");
        } else {
          element("traffic-status").textContent =
            "Live traffic update failed: " + error.message + ". Retrying in 2 seconds.";
        }
      } finally {
        trafficRequestInFlight = false;
      }
    }

    async function startTrafficSampling() {
      trafficSamplingAllowed = true;
      if (trafficPollTimer !== null) {
        window.clearInterval(trafficPollTimer);
        trafficPollTimer = null;
      }
      resetTrafficHistory();
      if (document.hidden) {
        element("traffic-status").textContent = "Sampling will start when this tab is visible.";
        return;
      }
      await pollTraffic();
      if (trafficSamplingAllowed && trafficPollTimer === null) {
        trafficPollTimer = window.setInterval(pollTraffic, TRAFFIC_POLL_INTERVAL_MS);
      }
    }

    function refreshAfterSuccess(message) {
      showMessage(message, "success");
      refresh().catch((error) => {
        showMessage(message + " Dashboard refresh failed: " + error.message, "error");
      });
    }

    function setButtonBusy(button, busy) {
      button.disabled = busy;
      if (busy) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
    }

    let toastTimer = null;

    function showToast(message, kind) {
      const toast = element("toast");
      const icon = element("toast-icon");
      if (toastTimer !== null) {
        window.clearTimeout(toastTimer);
        toastTimer = null;
      }
      element("toast-message").textContent = message;
      toast.className = "toast " + (kind || "working");
      toast.setAttribute("aria-live", kind === "error" ? "assertive" : "polite");
      icon.textContent = kind === "success" ? "✓" : kind === "error" ? "!" : "";
      toast.hidden = false;
      if (kind === "success") {
        toastTimer = window.setTimeout(() => {
          toast.hidden = true;
          toastTimer = null;
        }, 6000);
      }
    }

    function dismissToast() {
      if (toastTimer !== null) {
        window.clearTimeout(toastTimer);
        toastTimer = null;
      }
      element("toast").hidden = true;
    }

    document.addEventListener("submit", (event) => {
      const label = event.submitter ? event.submitter.textContent.trim() : "Save";
      const saving = label.toLowerCase().startsWith("save ")
        ? "Saving " + label.slice(5) + "..."
        : label + " in progress...";
      if (event.submitter) {
        pendingFormSubmitButton = event.submitter;
        setButtonBusy(event.submitter, true);
      }
      showMessage(saving, "working");
    }, true);

    function renderMode(data) {
      helperConfigured = data.helper_configured;
      const badge = element("mode-badge");
      const notice = element("mode-notice");
      badge.textContent = helperConfigured ? "Hardware mode" : "Configuration only";
      badge.className = "badge " + (helperConfigured ? "hardware" : "configuration");
      notice.textContent = helperConfigured
        ? "Hardware mode is active. Network actions can update the live test network."
        : "Configuration-only mode is active. Changes are saved but live network actions are unavailable.";
      element("apply-button").disabled = !helperConfigured;
      element("reset-button").disabled = !helperConfigured;
      element("scan-observations").disabled = !helperConfigured;
    }

    function renderRuntimeSummary(state) {
      const labels = {
        unknown: "No network action has run yet",
        applying: "Applying configuration...",
        resetting: "Resetting network...",
        applied: "Configuration applied successfully",
        reset: "Network reset successfully",
        error: "The last network action failed"
      };
      const summary = element("runtime-summary");
      summary.textContent = state.observed_state === "unknown" && state.last_action
        ? "Settings changed since the last network action"
        : labels[state.observed_state] || "Runtime state unavailable";
      summary.className = "runtime-summary " + state.observed_state;
    }

    function renderConnectedDevices(data) {
      const container = element("connected-devices");
      const count = element("connected-count");
      container.replaceChildren();
      if (!data.available) {
        count.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Connected-client information is unavailable.";
        container.appendChild(empty);
        return;
      }
      const devices = data.devices || [];
      count.textContent = devices.length + " connected";
      if (devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = "No IPv4 clients are currently visible.";
        container.appendChild(empty);
        return;
      }
      devices.forEach((device) => {
        const row = document.createElement("div");
        row.className = "connected-row";
        const dot = document.createElement("span");
        dot.className = "connected-dot";
        const details = document.createElement("div");
        const address = document.createElement("p");
        address.className = "connected-address";
        address.textContent = device.ipv4_address;
        const metadata = document.createElement("p");
        metadata.className = "connected-meta";
        metadata.textContent = device.mac_address;
        details.append(address, metadata);
        const state = document.createElement("span");
        state.className = "connected-state";
        state.textContent = device.state;
        const controls = document.createElement("div");
        controls.className = "connected-controls";
        const profileSelect = document.createElement("select");
        profileSelect.setAttribute("aria-label", "Profile for " + device.ipv4_address);
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = profiles.length ? "Choose profile..." : "No profiles saved";
        profileSelect.appendChild(emptyOption);
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = profile.name + " (" + profile.id + ")";
          option.selected = profile.id === device.assigned_profile_id;
          profileSelect.appendChild(option);
        });
        const applyButton = document.createElement("button");
        applyButton.type = "button";
        applyButton.textContent = device.assigned_profile_id ? "Apply profile" : "Assign profile";
        applyButton.disabled = !helperConfigured || profiles.length === 0;
        profileSelect.addEventListener("change", () => {
          applyButton.disabled = !helperConfigured || profileSelect.value === "";
        });
        applyButton.addEventListener("click", () => {
          applyProfileToDevice(device, profileSelect.value, applyButton);
        });
        controls.append(profileSelect, applyButton);
        row.append(dot, details, state, controls);
        container.appendChild(row);
      });
    }

    function renderUplink(data) {
      const container = element("uplink-details");
      const state = element("uplink-state");
      const note = element("uplink-note");
      const routerLink = element("router-settings-link");
      container.replaceChildren();
      if (!data.available || !data.uplink) {
        state.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Upstream connection information is unavailable.";
        container.appendChild(empty);
        note.textContent = "The Pi can use the TG3442DE controls in hardware mode. Keep the Pi connected by Ethernet and enter the router password only when you use a router action.";
        return;
      }
      state.textContent = "Detected on " + (data.uplink.interface || "Ethernet");
      [
        ["Pi interface", data.uplink.interface || "Unknown"],
        ["Pi Ethernet connection", data.uplink.connection_name || "Unknown"],
        ["Router gateway", data.uplink.gateway || "Unknown"]
      ].forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "uplink-value";
        const labelElement = document.createElement("span");
        labelElement.textContent = label;
        const valueElement = document.createElement("strong");
        valueElement.textContent = value;
        item.append(labelElement, valueElement);
        container.appendChild(item);
      });
      if (data.router && data.router.settings_url) {
        routerLink.href = data.router.settings_url;
      }
      note.textContent = "The router controls below use the TG3442DE local interface. Keep Ethernet connected so the Pi remains reachable if router Wi-Fi is turned off.";
    }

    async function applyProfileToDevice(device, profileId, button) {
      if (!profileId) {
        showMessage("Choose a profile before applying it.", "error");
        return;
      }
      setActionBusy(true, "/api/apply");
      setButtonBusy(button, true);
      showMessage("Applying " + profileId + " to " + device.ipv4_address + "...", "working");
      try {
        await request("/api/connected-devices/apply-profile", "POST", {
          ipv4_address: device.ipv4_address,
          mac_address: device.mac_address,
          profile_id: profileId
        });
        refreshAfterSuccess("Applied " + profileId + " to " + device.ipv4_address + ".");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
        setActionBusy(false, "/api/apply");
      }
    }

    async function loadConnectedDevices() {
      if (connectedDevicesRequestInFlight) return;
      connectedDevicesRequestInFlight = true;
      try {
        renderConnectedDevices(await request("/api/connected-devices", "GET"));
      } catch (error) {
        renderConnectedDevices({ available: false, devices: [], message: error.message });
      } finally {
        connectedDevicesRequestInFlight = false;
      }
    }

    async function loadUplink() {
      try {
        renderUplink(await request("/api/uplink", "GET"));
      } catch (error) {
        renderUplink({ available: false, message: error.message });
      }
    }

    function renderObservations(data) {
      const container = element("observations");
      const count = element("observation-count");
      observedDomainsByDevice = {};
      container.replaceChildren();
      if (!data.available) {
        count.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Traffic discovery is unavailable.";
        container.appendChild(empty);
        return;
      }
      const devices = data.devices || [];
      count.textContent = (data.total_domains || 0) + " domains";
      if (devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = "No DNS observations yet. Connect a device and run a scan.";
        container.appendChild(empty);
        return;
      }
      devices.forEach((device) => {
        if (device.assigned_device_id) {
          observedDomainsByDevice[device.assigned_device_id] = (device.domains || [])
            .map((observation) => observation.domain);
        }
        const card = document.createElement("div");
        card.className = "observation-device";
        const heading = document.createElement("div");
        heading.className = "observation-device-heading";
        const title = document.createElement("h3");
        title.textContent = device.assigned_device_name || device.ipv4_address;
        const address = document.createElement("p");
        address.textContent = device.ipv4_address;
        heading.append(title, address);
        const targetProfile = document.createElement("select");
        targetProfile.setAttribute("aria-label", "Profile for discovered domains");
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = "Add to " + profile.name;
          option.selected = profile.id === device.assigned_profile_id;
          targetProfile.appendChild(option);
        });
        const targetRow = document.createElement("div");
        targetRow.className = "observation-actions";
        targetRow.append(targetProfile);
        card.append(heading, targetRow);
        const list = document.createElement("ul");
        list.className = "observation-domain-list";
        (device.domains || []).forEach((observation) => {
          const item = document.createElement("li");
          item.className = "observation-domain";
          const domain = document.createElement("span");
          domain.textContent = observation.domain;
          const queryCount = document.createElement("span");
          queryCount.className = "observation-domain-count";
          queryCount.textContent = observation.query_count + " queries";
          const profileButton = document.createElement("button");
          profileButton.type = "button";
          profileButton.textContent = "Add to profile";
          profileButton.disabled = profiles.length === 0;
          profileButton.addEventListener("click", () => addDomainToProfile(
            observation.domain,
            targetProfile.value,
            profileButton
          ));
          const blockButton = document.createElement("button");
          blockButton.type = "button";
          blockButton.textContent = "Block globally";
          blockButton.addEventListener("click", () => addGlobalBlock(
            observation.domain,
            blockButton
          ));
          item.append(domain, queryCount, profileButton, blockButton);
          list.appendChild(item);
        });
        card.append(list);
        container.appendChild(card);
      });
    }

    async function addDomainToProfile(domain, profileId, button) {
      if (!profileId) return;
      setButtonBusy(button, true);
      showMessage("Adding " + domain + " to the profile...", "working");
      try {
        await request("/api/observations/add-to-profile", "POST", {
          domain: domain,
          profile_id: profileId
        });
        refreshAfterSuccess("Added " + domain + " to the profile.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function addGlobalBlock(domain, button) {
      setButtonBusy(button, true);
      showMessage("Adding " + domain + " to the global block list...", "working");
      try {
        await request("/api/observations/add-global-block", "POST", { domain: domain });
        refreshAfterSuccess("Added " + domain + " to the global block list.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function loadObservations() {
      try {
        renderObservations(await request("/api/observations", "GET"));
      } catch (error) {
        const message = error.status === 401
          ? "Enter the admin token to view observed domains."
          : error.message;
        renderObservations({ available: false, devices: [], message: message });
      }
    }

    function formatQuotaBytes(bytes) {
      if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(2) + " GB";
      if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
      if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
      return bytes + " bytes";
    }

    function quotaResetText(quota) {
      if (quota.reset_mode === "interval") {
        return "resets every " + Math.round(quota.reset_interval_seconds / 60) + " minutes";
      }
      if (quota.reset_mode === "daily") {
        return "resets daily at " + (quota.reset_times || []).map((minute) =>
          String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
          String(minute % 60).padStart(2, "0")
        ).join(", ");
      }
      return "manual reset";
    }

    function quotaSchedulePayload(prefix) {
      const mode = element(prefix + "-reset-mode").value;
      let intervalSeconds = 0;
      let resetTimes = [];
      if (mode === "interval") {
        intervalSeconds = Number(element(prefix + "-interval").value) * 60;
      } else if (mode === "daily") {
        resetTimes = element(prefix + "-times").value.split(",")
          .map((value) => value.trim())
          .filter(Boolean)
          .map((value) => {
            const match = /^(\d{1,2}):(\d{2})$/.exec(value);
            if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) {
              throw new Error("Reset times must use HH:MM format.");
            }
            return Number(match[1]) * 60 + Number(match[2]);
          });
        if (!resetTimes.length) throw new Error("Add at least one daily reset time.");
      }
      return {
        reset_mode: mode,
        reset_interval_seconds: intervalSeconds,
        reset_times: resetTimes
      };
    }

    function syncQuotaReset(prefix) {
      const mode = element(prefix + "-reset-mode").value;
      element(prefix + "-interval-field").hidden = mode !== "interval";
      element(prefix + "-times-field").hidden = mode !== "daily";
    }

    function setQuotaAmount(prefix, bytes) {
      const unit = bytes >= 1073741824 && bytes % 1073741824 === 0
        ? 1073741824
        : 1048576;
      element(prefix + "-unit").value = String(unit);
      element(prefix + "-amount").value = Math.max(1, Math.round(bytes / unit));
    }

    function quotaRowProgress(quota) {
      return Math.min(100, Math.round((quota.used_bytes / quota.limit_bytes) * 100));
    }

    function createQuotaRow({ quota, service }) {
      const row = document.createElement("div");
      row.className = "quota-row" + (quota.reached ? " reached" : "");
      const heading = document.createElement("div");
      heading.className = "quota-row-heading";
      const title = document.createElement("strong");
      title.textContent = service === "group"
        ? quota.group_name + " · " + quota.profile_name
        : service
          ? quota.name + " · " + quota.device_name
          : quota.device_name + " · " + quota.profile_name;
      const status = document.createElement("span");
      status.className = "quota-status";
      status.textContent = quota.reached ? "Limit reached" : quotaResetText(quota);
      heading.append(title, status);
      const detail = document.createElement("span");
      detail.textContent = formatQuotaBytes(quota.used_bytes) + " used of " +
        formatQuotaBytes(quota.limit_bytes) + " · " + formatQuotaBytes(quota.remaining_bytes) + " left" +
        (service ? " · weight " + (quota.weight_percent / 100).toFixed(1) + "×" : "");
      const meter = document.createElement("div");
      meter.className = "quota-meter";
      const meterValue = document.createElement("span");
      meterValue.style.width = quotaRowProgress(quota) + "%";
      meter.appendChild(meterValue);
      const actions = document.createElement("div");
      actions.className = "quota-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "Edit";
      editButton.addEventListener("click", () => service === "group"
        ? editGroupDataLimit(quota)
        : service
          ? editServiceQuota(quota)
          : editDataLimit(quota));
      const resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.textContent = "Reset usage";
      resetButton.addEventListener("click", () => resetQuota(quota, service, resetButton));
      actions.append(editButton, resetButton);
      if (service) {
        const domains = document.createElement("span");
        domains.textContent = "Domains: " + quota.domains.join(", ");
        actions.append(domains);
      }
      row.append(heading, detail, meter, actions);
      return row;
    }

    function renderDataLimits(data) {
      const limits = data.data_limits || [];
      const container = element("data-limit-list");
      element("data-limit-count").textContent = limits.length + (limits.length === 1 ? " limit" : " limits");
      container.replaceChildren();
      if (!limits.length) {
        container.textContent = data.error || "No device limits saved.";
        return;
      }
      limits.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: false })));
    }

    function renderGroups(data) {
      groups = data.groups || [];
      const container = element("group-list");
      container.replaceChildren();
      if (!groups.length) {
        container.textContent = data.error || "No groups saved.";
        return;
      }
      groups.forEach((group) => {
        const row = document.createElement("div");
        row.className = "quota-row";
        const heading = document.createElement("div");
        heading.className = "quota-row-heading";
        const title = document.createElement("strong");
        title.textContent = group.name + " · " + group.id;
        const profile = document.createElement("span");
        profile.className = "quota-status";
        profile.textContent = group.profile_name
          ? "Profile: " + group.profile_name
          : "Each device keeps its profile";
        heading.append(title, profile);
        const members = document.createElement("span");
        members.textContent = "Devices: " + group.devices.map((device) => device.name).join(", ");
        const actions = document.createElement("div");
        actions.className = "quota-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editGroup(group));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteGroup(group, deleteButton));
        actions.append(editButton, deleteButton);
        row.append(heading, members, actions);
        container.appendChild(row);
      });
    }

    function renderGroupDataLimits(data) {
      const limits = data.group_data_limits || [];
      const container = element("group-data-limit-list");
      element("group-data-limit-count").textContent = limits.length +
        (limits.length === 1 ? " group limit" : " group limits");
      container.replaceChildren();
      if (!limits.length) {
        container.textContent = data.error || "No shared group limits saved.";
        return;
      }
      limits.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: "group" })));
    }

    function renderServiceQuotas(data) {
      const quotas = data.service_quotas || [];
      const container = element("service-quota-list");
      element("service-quota-count").textContent = quotas.length + (quotas.length === 1 ? " service limit" : " service limits");
      container.replaceChildren();
      if (!quotas.length) {
        container.textContent = data.error || "No service limits saved.";
        return;
      }
      quotas.forEach((quota) => container.appendChild(createQuotaRow({ quota, service: true })));
    }

    function renderTimeQuotas(data) {
      const quotas = data.time_quotas || [];
      const container = element("time-quota-list");
      element("time-quota-count").textContent = quotas.length + (quotas.length === 1 ? " time limit" : " time limits");
      container.replaceChildren();
      if (!quotas.length) {
        container.textContent = data.error || "No time limits saved.";
        return;
      }
      quotas.forEach((quota) => container.appendChild(createTimeQuotaRow(quota)));
    }

    function populateQuotaDomains(prefix, selectedDomains) {
      const select = element(prefix + "-domains");
      const domains = observedDomainsByDevice[element(prefix + "-device").value] || [];
      select.replaceChildren();
      if (!domains.length) {
        const option = document.createElement("option");
        option.textContent = "No observed domains for this device";
        option.disabled = true;
        select.appendChild(option);
        return;
      }
      domains.forEach((domain) => {
        const option = document.createElement("option");
        option.value = domain;
        option.textContent = domain;
        option.selected = (selectedDomains || []).includes(domain);
        select.appendChild(option);
      });
    }

    function populateServiceQuotaDomains(selectedDomains) {
      populateQuotaDomains("service-quota", selectedDomains);
    }

    function populateTimeQuotaDomains(selectedDomains) {
      populateQuotaDomains("time-quota", selectedDomains);
    }

    function editDataLimit(quota) {
      element("data-limit-device").value = quota.device_id;
      element("data-limit-profile").value = quota.profile_id;
      setQuotaAmount("data-limit", quota.limit_bytes);
      element("data-limit-reset-mode").value = quota.reset_mode;
      element("data-limit-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("data-limit-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("data-limit-enabled").checked = quota.enabled;
      syncQuotaReset("data-limit");
      element("data-limit-form").scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function editGroup(group) {
      element("group-id").value = group.id;
      element("group-name").value = group.name;
      element("group-profile").value = group.profile_id || "";
      Array.from(element("group-devices").options).forEach((option) => {
        option.selected = group.device_ids.includes(option.value);
      });
      element("group-form").scrollIntoView({ behavior: "smooth", block: "center" });
    }

    async function deleteGroup(group, button) {
      setButtonBusy(button, true);
      showMessage("Deleting the device group...", "working");
      try {
        await request("/api/groups/delete", "POST", { id: group.id });
        refreshAfterSuccess("Device group deleted.");
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function editGroupDataLimit(quota) {
      element("group-data-limit-group").value = quota.group_id;
      element("group-data-limit-profile").value = quota.profile_id;
      setQuotaAmount("group-data-limit", quota.limit_bytes);
      element("group-data-limit-reset-mode").value = quota.reset_mode;
      element("group-data-limit-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("group-data-limit-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
        String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("group-data-limit-enabled").checked = quota.enabled;
      syncQuotaReset("group-data-limit");
      element("group-data-limit-form").scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function editServiceQuota(quota) {
      element("service-quota-id").value = quota.id;
      element("service-quota-name").value = quota.name;
      element("service-quota-device").value = quota.device_id;
      element("service-quota-profile").value = quota.profile_id;
      setQuotaAmount("service-quota", quota.limit_bytes);
      element("service-quota-weight").value = (quota.weight_percent / 100).toFixed(1);
      element("service-quota-reset-mode").value = quota.reset_mode;
      element("service-quota-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("service-quota-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" + String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("service-quota-enabled").checked = quota.enabled;
      populateServiceQuotaDomains(quota.domains);
      syncQuotaReset("service-quota");
      element("service-quota-form").scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function formatQuotaDuration(seconds) {
      if (seconds >= 3600 && seconds % 3600 === 0) return Math.floor(seconds / 3600) + " hours";
      if (seconds >= 60 && seconds % 60 === 0) return Math.floor(seconds / 60) + " minutes";
      return seconds + " seconds";
    }

    function editTimeQuota(quota) {
      element("time-quota-id").value = quota.id;
      element("time-quota-name").value = quota.name;
      element("time-quota-device").value = quota.device_id;
      element("time-quota-profile").value = quota.profile_id;
      const unit = quota.limit_seconds >= 3600 && quota.limit_seconds % 3600 === 0 ? 3600 : 60;
      element("time-quota-unit").value = String(unit);
      element("time-quota-amount").value = Math.max(1, Math.round(quota.limit_seconds / unit));
      element("time-quota-window").value = Math.max(1, Math.round(quota.window_duration_seconds / 60));
      element("time-quota-reset-mode").value = quota.reset_mode;
      element("time-quota-interval").value = Math.max(1, Math.round(quota.reset_interval_seconds / 60));
      element("time-quota-times").value = (quota.reset_times || []).map((minute) =>
        String(Math.floor(minute / 60)).padStart(2, "0") + ":" +
        String(minute % 60).padStart(2, "0")
      ).join(", ");
      element("time-quota-enabled").checked = quota.enabled;
      populateTimeQuotaDomains(quota.domains);
      syncQuotaReset("time-quota");
      element("time-quota-form").scrollIntoView({ behavior: "smooth", block: "center" });
    }

    async function timeQuotaAction(quota, action, button) {
      setButtonBusy(button, true);
      showMessage(action === "start" ? "Starting the time limit..." : "Stopping the time limit...", "working");
      try {
        const result = await request("/api/time-quotas/" + action, "POST", { id: quota.id });
        refreshAfterSuccess(
          result.live_applied
            ? (action === "start" ? "Time quota timer started and applied." : "Time quota updated and applied.")
            : (action === "start" ? "Time quota timer started." : "Time quota updated.")
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function resetTimeQuota(quota, button) {
      setButtonBusy(button, true);
      showMessage("Resetting time limit usage...", "working");
      try {
        await request("/api/time-quotas/reset", "POST", { id: quota.id });
        showMessage("Time quota usage reset.", "success");
        loadQuotas();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function createTimeQuotaRow(quota) {
      const row = document.createElement("div");
      row.className = "quota-row" + (quota.reached ? " reached" : "");
      const heading = document.createElement("div");
      heading.className = "quota-row-heading";
      const title = document.createElement("strong");
      title.textContent = quota.name + " · " + quota.device_name;
      const status = document.createElement("span");
      status.className = "quota-status";
      status.textContent = quota.reached
        ? "Limit reached"
        : (quota.running ? formatQuotaDuration(quota.window_remaining_seconds) + " left in timer" : "Timer stopped");
      heading.append(title, status);
      const detail = document.createElement("span");
      detail.textContent = formatQuotaDuration(quota.used_seconds) + " used of " +
        formatQuotaDuration(quota.limit_seconds) + " · timer window " +
        formatQuotaDuration(quota.window_duration_seconds) + " · " + quotaResetText(quota);
      const meter = document.createElement("div");
      meter.className = "quota-meter";
      const meterValue = document.createElement("span");
      meterValue.style.width = Math.min(100, Math.round((quota.used_seconds / quota.limit_seconds) * 100)) + "%";
      meter.appendChild(meterValue);
      const actions = document.createElement("div");
      actions.className = "quota-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.textContent = "Edit";
      editButton.addEventListener("click", () => editTimeQuota(quota));
      const timerButton = document.createElement("button");
      timerButton.type = "button";
      timerButton.textContent = quota.running ? "Stop timer" : "Start timer";
      timerButton.addEventListener("click", () => timeQuotaAction(quota, quota.running ? "stop" : "start", timerButton));
      const resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.textContent = "Reset usage";
      resetButton.addEventListener("click", () => resetTimeQuota(quota, resetButton));
      const domains = document.createElement("span");
      domains.textContent = "Domains: " + quota.domains.join(", ");
      actions.append(editButton, timerButton, resetButton, domains);
      row.append(heading, detail, meter, actions);
      return row;
    }

    async function resetQuota(quota, service, button) {
      setButtonBusy(button, true);
      showMessage("Resetting quota usage...", "working");
      try {
        const path = service === "group"
          ? "/api/group-data-limits/reset"
          : service ? "/api/service-quotas/reset" : "/api/data-limits/reset";
        const body = service === "group"
          ? { group_id: quota.group_id }
          : service ? { id: quota.id } : { device_id: quota.device_id };
        await request(path, "POST", body);
        showMessage("Quota usage reset.", "success");
        loadQuotas();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function loadQuotas() {
      try {
        const [dataLimits, groupDataLimits, serviceQuotas, timeQuotas] = await Promise.all([
          request("/api/data-limits", "GET"),
          request("/api/group-data-limits", "GET"),
          request("/api/service-quotas", "GET"),
          request("/api/time-quotas", "GET")
        ]);
        renderDataLimits(dataLimits);
        renderGroupDataLimits(groupDataLimits);
        renderServiceQuotas(serviceQuotas);
        renderTimeQuotas(timeQuotas);
      } catch (error) {
        renderDataLimits({ data_limits: [], error: "Enter the admin token to view device limits." });
        renderGroupDataLimits({ group_data_limits: [], error: "Enter the admin token to view group limits." });
        renderServiceQuotas({ service_quotas: [], error: "Enter the admin token to view service limits." });
        renderTimeQuotas({ time_quotas: [], error: "Enter the admin token to view time limits." });
      }
    }

    async function scanObservations() {
      if (!helperConfigured || observationRequestInFlight) return;
      observationRequestInFlight = true;
      const scanButton = element("scan-observations");
      setButtonBusy(scanButton, true);
      scanButton.textContent = "Scanning...";
      showMessage("Sampling DNS queries from the test network...", "working");
      try {
        const result = await request("/api/observations/scan", "POST", {});
        showMessage(
          "Traffic scan complete: " + result.captured_observations + " domain observations captured.",
          "success"
        );
        loadObservations();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        observationRequestInFlight = false;
        scanButton.disabled = !helperConfigured;
        scanButton.removeAttribute("aria-busy");
        scanButton.textContent = "Scan now";
      }
    }

    async function loadHealth() {
      renderMode(await request("/api/health", "GET"));
    }

    async function loadProfiles() {
      const data = await request("/api/profiles", "GET");
      profiles = data.profiles;
      element("profiles").textContent = JSON.stringify(profiles, null, 2);
      [
        "schedule-profile",
        "timer-profile",
        "progression-start-profile",
        "progression-end-profile",
        "data-limit-profile",
        "service-quota-profile",
        "time-quota-profile",
        "group-profile",
        "group-data-limit-profile"
      ].forEach((selectId) => {
        const profileSelect = element(selectId);
        profileSelect.replaceChildren();
        if (selectId === "group-profile") {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "Use each device profile";
          profileSelect.appendChild(option);
        }
        profiles.forEach((profile) => {
          const option = document.createElement("option");
          option.value = profile.id;
          option.textContent = profile.name + " (" + profile.id + ")";
          profileSelect.appendChild(option);
        });
      });
    }

    async function loadDevicesForSchedule() {
      const data = await request("/api/devices", "GET");
      [
        "schedule-device",
        "timer-device",
        "progression-device",
        "data-limit-device",
        "service-quota-device",
        "time-quota-device",
        "group-devices"
      ].forEach((selectId) => {
        const deviceSelect = element(selectId);
        deviceSelect.replaceChildren();
      data.devices.forEach((device) => {
          const option = document.createElement("option");
          option.value = device.id;
          option.textContent = device.name + " (" + device.ipv4_address + ")";
          deviceSelect.appendChild(option);
        });
      });
      updateTrafficDeviceOptions(data.devices);
      populateServiceQuotaDomains();
      populateTimeQuotaDomains();
    }

    async function loadGroups() {
      try {
        const data = await request("/api/groups", "GET");
        renderGroups(data);
        const groupSelect = element("group-data-limit-group");
        const selectedGroup = groupSelect.value;
        groupSelect.replaceChildren();
        groups.forEach((group) => {
          const option = document.createElement("option");
          option.value = group.id;
          option.textContent = group.name + " (" + group.device_ids.length + " devices)";
          option.selected = group.id === selectedGroup;
          groupSelect.appendChild(option);
        });
      } catch (error) {
        renderGroups({ groups: [], error: error.message });
        element("group-data-limit-group").replaceChildren();
      }
    }

    function renderSchedules(schedules) {
      const container = element("schedule-list");
      container.replaceChildren();
      if (!schedules.length) {
        container.textContent = "No schedules yet.";
        return;
      }
      schedules.forEach((schedule) => {
        const row = document.createElement("div");
        row.className = "schedule-row";
        const name = document.createElement("span");
        name.textContent = schedule.id + " · " + schedule.device_id + " → " + schedule.profile_id;
        const time = document.createElement("span");
        time.textContent = schedule.days_of_week.join(",") + " · " +
          String(Math.floor(schedule.start_minute / 60)).padStart(2, "0") + ":" +
          String(schedule.start_minute % 60).padStart(2, "0") + "–" +
          String(Math.floor(schedule.end_minute / 60)).padStart(2, "0") + ":" +
          String(schedule.end_minute % 60).padStart(2, "0");
        row.append(name, time);
        container.appendChild(row);
      });
    }

    async function loadSchedules() {
      const data = await request("/api/schedules", "GET");
      renderSchedules(data.schedules);
    }

    function formatTimerRemaining(seconds) {
      const minutes = Math.floor(seconds / 60);
      const remainingSeconds = seconds % 60;
      if (minutes === 0) return remainingSeconds + " seconds";
      if (remainingSeconds === 0) return minutes + " minutes";
      return minutes + "m " + remainingSeconds + "s";
    }

    function renderTimers(data) {
      const container = element("timer-list");
      const count = element("timer-count");
      const timers = data.timers || [];
      container.replaceChildren();
      count.textContent = timers.length + (timers.length === 1 ? " active timer" : " active timers");
      if (timers.length === 0) {
        container.textContent = "No active timers.";
        return;
      }
      timers.forEach((timer) => {
        const row = document.createElement("div");
        row.className = "timer-row";
        const details = document.createElement("div");
        details.className = "timer-details";
        const title = document.createElement("strong");
        title.textContent = timer.device_name + " → " + timer.profile_name;
        const remaining = document.createElement("span");
        remaining.textContent = formatTimerRemaining(timer.remaining_seconds) + " remaining";
        details.append(title, remaining);
        const cancelButton = document.createElement("button");
        cancelButton.type = "button";
        cancelButton.textContent = "Cancel";
        cancelButton.addEventListener("click", () => cancelTimer(timer.id, cancelButton));
        row.append(details, cancelButton);
        container.appendChild(row);
      });
    }

    async function loadTimers() {
      try {
        renderTimers(await request("/api/timers", "GET"));
      } catch (error) {
        renderTimers({ timers: [] });
      }
    }

    async function cancelTimer(timerId, button) {
      setButtonBusy(button, true);
      showMessage("Cancelling the timer...", "working");
      try {
        const result = await request("/api/timers/cancel", "POST", { id: timerId });
        refreshAfterSuccess(
          result.live_applied ? "Timer cancelled and the normal profile is active." : "Timer cancelled.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function renderProgressions(data) {
      const container = element("progression-list");
      const count = element("progression-count");
      const progressions = data.progressions || [];
      container.replaceChildren();
      count.textContent = progressions.length + (
        progressions.length === 1 ? " active progression" : " active progressions"
      );
      if (progressions.length === 0) {
        container.textContent = "No active progressions.";
        return;
      }
      progressions.forEach((progression) => {
        const row = document.createElement("div");
        row.className = "progression-row";
        const heading = document.createElement("div");
        heading.className = "progression-row-heading";
        const title = document.createElement("strong");
        title.textContent = progression.device_name + " · " +
          progression.start_profile_name + " → " + progression.end_profile_name;
        const status = document.createElement("span");
        const signal = progression.mode === "bytes"
          ? formatQuotaBytes(progression.traffic_bytes) + " / " + formatQuotaBytes(progression.traffic_target_bytes)
          : progression.mode === "traffic"
            ? progression.traffic_queries + " DNS queries"
            : "time based";
        status.textContent = progression.progress_percent + "% · " + signal + " · " +
          formatTimerRemaining(progression.remaining_seconds) + " left";
        heading.append(title, status);
        const meter = document.createElement("div");
        meter.className = "progression-meter";
        const meterValue = document.createElement("span");
        meterValue.style.width = progression.progress_percent + "%";
        meter.appendChild(meterValue);
        const stopButton = document.createElement("button");
        stopButton.type = "button";
        stopButton.textContent = "Stop and recover";
        stopButton.addEventListener("click", () => stopProgression(progression.id, stopButton));
        row.append(heading, meter, stopButton);
        container.appendChild(row);
      });
    }

    async function loadProgressions() {
      try {
        renderProgressions(await request("/api/progressions", "GET"));
      } catch (error) {
        renderProgressions({ progressions: [] });
      }
    }

    async function stopProgression(progressionId, button) {
      setButtonBusy(button, true);
      showMessage("Stopping the progression and restoring the normal profile...", "working");
      try {
        const result = await request("/api/progressions/stop", "POST", { id: progressionId });
        refreshAfterSuccess(
          result.live_applied
            ? "Progression stopped and the normal profile is active."
            : "Progression stopped."
        );
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    function syncProgressionMode() {
      const trafficMode = element("progression-mode").value === "traffic";
      const bytesMode = element("progression-mode").value === "bytes";
      element("progression-traffic-field").hidden = !trafficMode;
      element("progression-bytes-field").hidden = !bytesMode;
      element("progression-traffic-target").required = trafficMode;
      element("progression-bytes-target").required = bytesMode;
    }

    async function loadPolicies() {
      const data = await request("/api/policies", "GET");
      element("policies").textContent = JSON.stringify(data, null, 2);
      element("mac-mode").value = data.mac.mode;
      element("mac-allowlist").value = data.mac.allowlist.join(", ");
      element("mac-denylist").value = data.mac.denylist.join(", ");
      element("website-mode").value = data.websites.mode;
      element("website-allowlist").value = data.websites.allowed_domains.join(", ");
      element("website-blocklist").value = data.websites.blocked_domains.join(", ");
    }

    async function loadState() {
      const data = await request("/api/state", "GET");
      element("state").textContent = JSON.stringify(data.state, null, 2);
      renderRuntimeSummary(data.state);
    }

    async function loadAdminTools() {
      if (!element("admin-token").value.trim()) {
        element("diagnostics").textContent = "Enter the admin token to inspect diagnostics.";
        element("action-history").textContent = "Enter the admin token to view recent actions.";
        return { ok: false, message: "Enter the admin token first." };
      }
      try {
        const data = await request("/api/diagnostics", "GET");
        element("diagnostics").textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        element("diagnostics").textContent = error.message;
        element("action-history").textContent = error.message;
        return { ok: false, message: error.message };
      }
      try {
        const data = await request("/api/action-history", "GET");
        element("action-history").textContent = data.actions.length
          ? data.actions.map((action) => new Date(action.happened_at * 1000).toLocaleString() +
              " — " + action.action + " — " + action.status_code).join("\n")
          : "No admin actions have been recorded.";
      } catch (error) {
        element("action-history").textContent = error.message;
        return { ok: true, message: "Token accepted. Diagnostics loaded, but action history could not be loaded." };
      }
      return { ok: true, message: "Token accepted. Diagnostics loaded." };
    }

    async function checkAdminToken() {
      const button = element("check-admin-token");
      const status = element("token-check-status");
      if (!element("admin-token").value.trim()) {
        status.textContent = "Enter the admin token first.";
        showMessage("Enter the admin token first.", "error");
        element("admin-token").focus();
        return;
      }
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Checking...";
      status.textContent = "Checking the token and loading diagnostics...";
      showMessage("Checking the admin token...", "working");
      try {
        const result = await loadAdminTools();
        if (result.ok) await startTrafficSampling();
        status.textContent = result.ok ? result.message : "Token check failed: " + result.message;
        status.className = "token-check-status " + (result.ok ? "success" : "error");
        showMessage(status.textContent, result.ok ? "success" : "error");
        if (result.ok) {
          loadQuotas();
          loadObservations();
        }
      } catch (error) {
        status.textContent = "Token check failed: " + error.message;
        status.className = "token-check-status error";
        showMessage(status.textContent, "error");
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = "Check token";
      }
    }

    async function refresh() {
      await loadProfiles();
      await loadHealth();
      await loadObservations();
      await Promise.all([
        show("/api/devices", "devices", "devices"),
        show("/api/plan", "plan", "plan"),
        loadState(),
        loadDevicesForSchedule(),
        loadGroups(),
        loadSchedules(),
        loadTimers(),
        loadProgressions(),
        loadPolicies(),
        loadConnectedDevices(),
        loadUplink(),
        loadQuotas(),
        loadAdminTools()
      ]);
    }

    function setActionBusy(busy, path) {
      const applyButton = element("apply-button");
      const resetButton = element("reset-button");
      applyButton.disabled = busy || !helperConfigured;
      resetButton.disabled = busy || !helperConfigured;
      applyButton.setAttribute("aria-busy", String(busy && path === "/api/apply"));
      resetButton.setAttribute("aria-busy", String(busy && path === "/api/reset"));
      applyButton.textContent = busy && path === "/api/apply" ? "Applying..." : "Apply current configuration";
      resetButton.textContent = busy && path === "/api/reset" ? "Resetting..." : "Reset network";
    }

    async function runAction(path, successMessage, workingMessage) {
      if (!helperConfigured) {
        showMessage("Network actions are unavailable in configuration-only mode.", "error");
        return;
      }
      setActionBusy(true, path);
      showMessage(workingMessage, "working");
      try {
        const data = await request(path, "POST", {});
        if (data.state) renderRuntimeSummary(data.state);
        refreshAfterSuccess(successMessage);
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setActionBusy(false, path);
      }
    }

    element("apply-button").addEventListener("click", () => {
      runAction("/api/apply", "Configuration applied successfully.", "Applying configuration...");
    });

    element("reset-button").addEventListener("click", () => {
      runAction("/api/reset", "Network reset successfully.", "Resetting network...");
    });

    element("scan-observations").addEventListener("click", scanObservations);

    element("profile-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/profiles", "POST", {
          id: element("profile-id").value,
          name: element("profile-name").value,
          upload: impairment("upload"),
          download: impairment("download"),
          random_seed: optionalNumber("profile-seed"),
          reroll_interval_seconds: Number(element("profile-reroll").value),
          reroll_interval_randomness: {
            variation_percent: Number(element("profile-reroll-variation").value),
            enabled_probability: 100
          },
          outage_probability: Number(element("profile-outage-probability").value),
          outage_duration_seconds: Number(element("profile-outage-duration").value),
          outage_interval_seconds: Number(element("profile-outage-interval").value),
          outage_duration_randomness: { variation_percent: 0, enabled_probability: 100 },
          outage_interval_randomness: { variation_percent: 0, enabled_probability: 100 },
          website_mode: element("profile-website-mode").value,
          website_domains: commaList("profile-website-domains"),
          website_randomness: {
            variation_percent: 0,
            enabled_probability: Number(element("profile-website-enabled").value)
          }
        });
        refreshAfterSuccess("Profile saved.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/devices", "POST", {
          id: element("device-id").value,
          name: element("device-name").value,
          ipv4_address: element("device-ip").value,
          profile_id: element("device-profile").value,
          mac_address: element("device-mac").value.trim() || null
        });
        refreshAfterSuccess("Device saved.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("group-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const deviceIds = Array.from(element("group-devices").selectedOptions)
          .map((option) => option.value);
        if (!deviceIds.length) throw new Error("Select at least one device for the group.");
        await request("/api/groups", "POST", {
          id: element("group-id").value,
          name: element("group-name").value,
          profile_id: element("group-profile").value || null,
          device_ids: deviceIds
        });
        refreshAfterSuccess("Device group saved.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("policy-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/policies", "POST", {
          mac: {
            mode: element("mac-mode").value,
            allowlist: commaList("mac-allowlist"),
            denylist: commaList("mac-denylist")
          },
          websites: {
            mode: element("website-mode").value,
            allowed_domains: commaList("website-allowlist"),
            blocked_domains: commaList("website-blocklist")
          }
        });
        refreshAfterSuccess("Access rules saved. Apply the configuration to enforce them.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("schedule-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const days = selectedScheduleDays();
      if (!days.length) {
        showMessage("Choose at least one day for the schedule.", "error");
        return;
      }
      try {
        await request("/api/schedules", "POST", {
          id: element("schedule-id").value,
          device_id: element("schedule-device").value,
          profile_id: element("schedule-profile").value,
          days_of_week: days,
          start_minute: timeToMinute("schedule-start"),
          end_minute: timeToMinute("schedule-end"),
          enabled: true
        });
        refreshAfterSuccess("Schedule saved. Apply the configuration to start it.");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("timer-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/timers", "POST", {
          device_id: element("timer-device").value,
          profile_id: element("timer-profile").value,
          duration_minutes: Number(element("timer-duration").value)
        });
        refreshAfterSuccess(
          result.live_applied
            ? "Timer started and the profile is active now."
            : "Timer saved. Apply the configuration to start it.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("progression-mode").addEventListener("change", syncProgressionMode);
    syncProgressionMode();

    element("progression-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const mode = element("progression-mode").value;
        const result = await request("/api/progressions", "POST", {
          device_id: element("progression-device").value,
          start_profile_id: element("progression-start-profile").value,
          end_profile_id: element("progression-end-profile").value,
          mode: mode,
          duration_minutes: Number(element("progression-duration").value),
          traffic_target_queries: mode === "traffic"
            ? Number(element("progression-traffic-target").value)
            : 0,
          traffic_target_bytes: mode === "bytes"
            ? Number(element("progression-bytes-target").value) * 1048576
            : 0,
          website_domains: commaList("progression-domains")
        });
        refreshAfterSuccess(
          result.live_applied
            ? "Progression started and is active now."
            : "Progression saved. Apply the configuration to start it.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("data-limit-reset-mode").addEventListener("change", () => syncQuotaReset("data-limit"));
    element("group-data-limit-reset-mode").addEventListener("change", () => syncQuotaReset("group-data-limit"));
    element("service-quota-reset-mode").addEventListener("change", () => syncQuotaReset("service-quota"));
    element("time-quota-reset-mode").addEventListener("change", () => syncQuotaReset("time-quota"));
    element("service-quota-device").addEventListener("change", () => populateServiceQuotaDomains());
    element("time-quota-device").addEventListener("change", () => populateTimeQuotaDomains());
    element("check-admin-token").addEventListener("click", checkAdminToken);
    element("admin-token").addEventListener("input", () => {
      stopTrafficSampling("Token changed. Choose Check token to restart live traffic sampling.");
      element("token-check-status").textContent = "Token changed. Choose Check token to load diagnostics.";
      element("token-check-status").className = "token-check-status";
    });
    element("traffic-device").addEventListener("change", () => {
      if (!trafficSamplingAllowed) return;
      resetTrafficHistory();
      element("traffic-status").textContent = "Changing the device view...";
      pollTraffic();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (trafficPollTimer !== null) {
          window.clearInterval(trafficPollTimer);
          trafficPollTimer = null;
        }
        resetTrafficHistory();
        if (trafficSamplingAllowed) element("traffic-status").textContent = "Sampling paused while this tab is hidden.";
        return;
      }
      if (trafficSamplingAllowed) {
        element("traffic-status").textContent = "Resuming live traffic sampling...";
        pollTraffic().then(() => {
          if (trafficSamplingAllowed && trafficPollTimer === null) {
            trafficPollTimer = window.setInterval(pollTraffic, TRAFFIC_POLL_INTERVAL_MS);
          }
        });
      }
      drawTrafficChart();
    });
    window.addEventListener("resize", drawTrafficChart);
    element("toast-dismiss").addEventListener("click", dismissToast);
    element("refresh-diagnostics").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Refreshing...";
      showMessage("Refreshing diagnostics...", "working");
      try {
        const result = await loadAdminTools();
        if (result.ok) await startTrafficSampling();
        element("token-check-status").textContent = result.message;
        element("token-check-status").className = "token-check-status " + (result.ok ? "success" : "error");
        showMessage(result.message, result.ok ? "success" : "error");
      } catch (error) {
        element("token-check-status").textContent = error.message;
        element("token-check-status").className = "token-check-status error";
        showMessage(error.message, "error");
      } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.textContent = "Refresh";
      }
    });
    element("download-backup").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const token = element("admin-token").value.trim();
      if (!token) {
        element("action-history").textContent = "Enter the admin token before downloading a backup.";
        showMessage("Enter the admin token before downloading a backup.", "error");
        return;
      }
      setButtonBusy(button, true);
      button.textContent = "Preparing backup...";
      showMessage("Preparing the configuration backup...", "working");
      try {
        const response = await fetch("/api/backup", {
          headers: { Authorization: "Bearer " + token },
          cache: "no-store"
        });
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.error || response.statusText);
        }
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = "jittersim-backup.db";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
        showMessage("Backup downloaded.", "success");
      } catch (error) {
        element("action-history").textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
        button.textContent = "Download backup";
      }
    });
    syncQuotaReset("data-limit");
    syncQuotaReset("group-data-limit");
    syncQuotaReset("service-quota");
    syncQuotaReset("time-quota");

    element("data-limit-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/data-limits", "POST", {
          device_id: element("data-limit-device").value,
          profile_id: element("data-limit-profile").value,
          limit_bytes: Number(element("data-limit-amount").value) * Number(element("data-limit-unit").value),
          enabled: element("data-limit-enabled").checked,
          ...quotaSchedulePayload("data-limit")
        });
        refreshAfterSuccess(
          result.live_applied ? "Device limit saved and applied." : "Device limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("group-data-limit-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const result = await request("/api/group-data-limits", "POST", {
          group_id: element("group-data-limit-group").value,
          profile_id: element("group-data-limit-profile").value,
          limit_bytes: Number(element("group-data-limit-amount").value) * Number(element("group-data-limit-unit").value),
          enabled: element("group-data-limit-enabled").checked,
          ...quotaSchedulePayload("group-data-limit")
        });
        refreshAfterSuccess(
          result.live_applied ? "Shared group limit saved and applied." : "Shared group limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("service-quota-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = Array.from(element("service-quota-domains").selectedOptions)
          .map((option) => option.value);
        if (!domains.length) throw new Error("Select at least one observed domain.");
        const result = await request("/api/service-quotas", "POST", {
          id: element("service-quota-id").value,
          name: element("service-quota-name").value,
          device_id: element("service-quota-device").value,
          profile_id: element("service-quota-profile").value,
          domains: domains,
          limit_bytes: Number(element("service-quota-amount").value) * Number(element("service-quota-unit").value),
          weight_percent: Math.round(Number(element("service-quota-weight").value) * 100),
          enabled: element("service-quota-enabled").checked,
          ...quotaSchedulePayload("service-quota")
        });
        refreshAfterSuccess(
          result.live_applied ? "Service limit saved and applied." : "Service limit saved.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("time-quota-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = Array.from(element("time-quota-domains").selectedOptions)
          .map((option) => option.value);
        if (!domains.length) throw new Error("Select at least one observed domain.");
        const result = await request("/api/time-quotas", "POST", {
          id: element("time-quota-id").value,
          name: element("time-quota-name").value,
          device_id: element("time-quota-device").value,
          profile_id: element("time-quota-profile").value,
          domains: domains,
          limit_seconds: Number(element("time-quota-amount").value) * Number(element("time-quota-unit").value),
          window_duration_seconds: Number(element("time-quota-window").value) * 60,
          enabled: element("time-quota-enabled").checked,
          ...quotaSchedulePayload("time-quota")
        });
        refreshAfterSuccess(
          result.live_applied ? "Time limit saved and applied." : "Time limit saved. Start its timer from the saved row.",
        );
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    element("access-point-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/access-point", "POST", {
          ssid: element("access-point-ssid").value,
          password: element("access-point-password").value
        });
        element("access-point-password").value = "";
        showMessage("Pi access point updated. Reconnect the test device if its Wi-Fi name changed.", "success");
      } catch (error) {
        showMessage(error.message, "error");
      }
    });

    async function routerAction(path, body, message, button) {
      const password = element("router-admin-password").value;
      const status = element("router-action-message");
      if (!password) {
        status.textContent = "Enter the router admin password first.";
        showMessage(status.textContent, "error");
        return;
      }
      setButtonBusy(button, true);
      status.textContent = message;
      showMessage(message, "working");
      try {
        const response = await request(path, "POST", { admin_password: password, ...body });
        const result = response.result || response;
        status.textContent = result.ssid
          ? "Copied " + result.ssid + " to the Pi. Reconnect test devices if needed."
          : (result.enabled ? "Router Wi-Fi is on." : "Router Wi-Fi is off.");
        showMessage(status.textContent, "success");
      } catch (error) {
        status.textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        element("router-admin-password").value = "";
        setButtonBusy(button, false);
      }
    }

    element("sync-router-ap").addEventListener("click", (event) => {
      routerAction("/api/router/sync-ap", {}, "Reading the router Wi-Fi settings...", event.currentTarget);
    });

    element("disable-router-wifi").addEventListener("click", (event) => {
      if (!window.confirm("Turn off both primary router Wi-Fi radios? Keep the Pi connected by Ethernet.")) return;
      routerAction(
        "/api/router/wifi",
        { enabled: false, confirm_disable: true },
        "Turning off router Wi-Fi...",
        event.currentTarget
      );
    });

    element("enable-router-wifi").addEventListener("click", (event) => {
      routerAction("/api/router/wifi", { enabled: true }, "Turning on router Wi-Fi...", event.currentTarget);
    });

    window.setInterval(loadConnectedDevices, 5000);
    window.setInterval(loadTimers, 5000);
    window.setInterval(loadProgressions, 5000);
    window.setInterval(loadQuotas, 5000);
    window.setInterval(loadObservations, 15000);

    refresh().catch((error) => {
      showMessage(error.message, "error");
      element("plan").textContent = error.message;
    });
  </script>
</body>
</html>
"""


def _impairment_payload(impairment: Impairment) -> Dict[str, Any]:
    return asdict(impairment)


def _profile_payload(profile: Profile) -> Dict[str, Any]:
    return asdict(profile)


def _device_payload(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "ipv4_address": str(device.ipv4_address),
        "profile_id": device.profile_id,
        "mac_address": device.mac_address,
    }


def _device_group_payload(
    group: DeviceGroup,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one device group."""

    profile = repository.get_profile(group.profile_id) if group.profile_id else None
    devices = [
        _device_payload(device)
        for device_id in group.device_ids
        if (device := repository.get_device(device_id)) is not None
    ]
    return {
        "id": group.id,
        "name": group.name,
        "device_ids": list(group.device_ids),
        "devices": devices,
        "profile_id": group.profile_id,
        "profile_name": profile.name if profile is not None else None,
    }


def _schedule_payload(schedule: Schedule) -> Dict[str, Any]:
    return asdict(schedule)


def _timer_payload(timer: Timer) -> Dict[str, Any]:
    return asdict(timer)


def _data_limit_payload(
    data_limit: DataLimit,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one device quota."""

    device = repository.get_device(data_limit.device_id)
    profile = repository.get_profile(data_limit.profile_id)
    return {
        **asdict(data_limit),
        "reset_times": list(data_limit.reset_times),
        "device_name": device.name if device is not None else data_limit.device_id,
        "profile_name": profile.name if profile is not None else data_limit.profile_id,
        "remaining_bytes": max(0, data_limit.limit_bytes - data_limit.used_bytes),
        "reached": data_limit.used_bytes >= data_limit.limit_bytes,
    }


def _group_data_limit_payload(
    data_limit: GroupDataLimit,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one shared group quota."""

    group = repository.get_device_group(data_limit.group_id)
    profile = repository.get_profile(data_limit.profile_id)
    return {
        **asdict(data_limit),
        "reset_times": list(data_limit.reset_times),
        "group_name": group.name if group is not None else data_limit.group_id,
        "device_ids": list(group.device_ids) if group is not None else [],
        "profile_name": profile.name if profile is not None else data_limit.profile_id,
        "remaining_bytes": max(0, data_limit.limit_bytes - data_limit.used_bytes),
        "reached": data_limit.used_bytes >= data_limit.limit_bytes,
    }


def _service_quota_payload(
    quota: ServiceQuota,
    *,
    repository: ConfigRepository,
) -> Dict[str, Any]:
    """Build the dashboard representation of one service quota."""

    device = repository.get_device(quota.device_id)
    profile = repository.get_profile(quota.profile_id)
    return {
        **asdict(quota),
        "domains": list(quota.domains),
        "reset_times": list(quota.reset_times),
        "device_name": device.name if device is not None else quota.device_id,
        "profile_name": profile.name if profile is not None else quota.profile_id,
        "remaining_bytes": max(0, quota.limit_bytes - quota.used_bytes),
        "reached": quota.used_bytes >= quota.limit_bytes,
    }


def _time_quota_payload(
    quota: TimeQuota,
    *,
    repository: ConfigRepository,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the dashboard representation of one timed service quota."""

    current_time = int(time.time()) if now is None else now
    device = repository.get_device(quota.device_id)
    profile = repository.get_profile(quota.profile_id)
    window_active = quota.started_at <= current_time < quota.expires_at
    return {
        **asdict(quota),
        "domains": list(quota.domains),
        "reset_times": list(quota.reset_times),
        "device_name": device.name if device is not None else quota.device_id,
        "profile_name": profile.name if profile is not None else quota.profile_id,
        "remaining_seconds": max(0, quota.limit_seconds - quota.used_seconds),
        "window_remaining_seconds": (
            max(0, quota.expires_at - current_time) if quota.expires_at else 0
        ),
        "window_active": window_active,
        "running": quota.started_at > 0 and window_active,
        "reached": window_active and quota.used_seconds >= quota.limit_seconds,
    }


def _progression_payload(
    progression: Progression,
    *,
    repository: ConfigRepository,
    now: int,
    traffic_bytes: int = 0,
) -> Dict[str, Any]:
    device = repository.get_device(progression.device_id)
    start_profile = repository.get_profile(progression.start_profile_id)
    end_profile = repository.get_profile(progression.end_profile_id)
    total_queries = repository.total_domain_queries(str(device.ipv4_address)) if device else 0
    factor = progression_factor(
        progression=progression,
        now=now,
        traffic_queries=total_queries,
        traffic_bytes=traffic_bytes,
    )
    return {
        **asdict(progression),
        "device_name": device.name if device is not None else progression.device_id,
        "start_profile_name": (
            start_profile.name
            if start_profile is not None
            else progression.start_profile_id
        ),
        "end_profile_name": (
            end_profile.name if end_profile is not None else progression.end_profile_id
        ),
        "progress_percent": round(factor * 100),
        "remaining_seconds": max(
            0,
            progression.started_at + progression.duration_seconds - now,
        ),
        "traffic_queries": max(0, total_queries - progression.traffic_baseline_queries),
        "traffic_bytes": max(0, traffic_bytes - progression.traffic_baseline_bytes),
    }


def _policies_payload(repository: ConfigRepository) -> Dict[str, Any]:
    mac_policy = repository.get_mac_policy()
    website_policy = repository.get_website_policy()
    return {
        "mac": asdict(mac_policy),
        "websites": asdict(website_policy),
    }


def _domain_observation_payload(observation: DomainObservation) -> Dict[str, Any]:
    return {
        "domain": observation.domain,
        "query_count": observation.query_count,
        "first_seen": observation.first_seen,
        "last_seen": observation.last_seen,
    }


def _runtime_state_payload(state: RuntimeState) -> Dict[str, Any]:
    return {
        "configuration_version": state.configuration_version,
        "observed_state": state.observed_state,
        "last_action": state.last_action,
        "last_result": state.last_result,
        "last_error": state.last_error,
    }


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


class ControlServer(HTTPServer):
    """Serve configuration and plan data from one repository."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        repository: ConfigRepository,
        admin_token: Optional[str] = None,
        helper_client: Optional[HelperClient] = None,
        scheduler: Optional[RuntimeScheduler] = None,
        tls_certfile: Optional[Path] = None,
        tls_keyfile: Optional[Path] = None,
    ) -> None:
        if admin_token == "":
            raise ValueError("admin_token must not be empty")
        if (tls_certfile is None) != (tls_keyfile is None):
            raise ValueError("TLS certificate and key must be provided together")
        tls_context = None
        if tls_certfile is not None and tls_keyfile is not None:
            tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_context.load_cert_chain(str(tls_certfile), str(tls_keyfile))
        bind_host = server_address[0]
        if tls_context is None and bind_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("TLS is required when the dashboard listens beyond this device")
        self.repository = repository
        self.admin_token = admin_token
        self.helper_client = helper_client
        self.scheduler = scheduler
        self.secure_transport = tls_context is not None
        super().__init__(server_address, ControlRequestHandler)
        if tls_context is not None:
            self.socket = tls_context.wrap_socket(self.socket, server_side=True)

    def response_for(self, path: str) -> Tuple[int, str, bytes]:
        """Build the response body for a supported GET path."""

        if path == "/":
            return HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8")
        if path == "/api/health":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "status": "ok",
                    "mode": "hardware" if self.helper_client is not None else "configuration-only",
                    "helper_configured": self.helper_client is not None,
                }
            )
        if path == "/api/profiles":
            profiles = [_profile_payload(profile) for profile in self.repository.list_profiles()]
            return HTTPStatus.OK, "application/json", _json_bytes({"profiles": profiles})
        if path == "/api/devices":
            devices = [_device_payload(device) for device in self.repository.list_devices()]
            return HTTPStatus.OK, "application/json", _json_bytes({"devices": devices})
        if path == "/api/groups":
            groups = [
                _device_group_payload(group, repository=self.repository)
                for group in self.repository.list_device_groups()
            ]
            return HTTPStatus.OK, "application/json", _json_bytes({"groups": groups})
        if path == "/api/schedules":
            schedules = [_schedule_payload(schedule) for schedule in self.repository.list_schedules()]
            return HTTPStatus.OK, "application/json", _json_bytes({"schedules": schedules})
        if path == "/api/timers":
            now = int(time.time())
            timers = []
            for timer in self.repository.list_active_timers(now=now):
                device = self.repository.get_device(timer.device_id)
                profile = self.repository.get_profile(timer.profile_id)
                timers.append(
                    {
                        **_timer_payload(timer),
                        "device_name": device.name if device is not None else timer.device_id,
                        "profile_name": profile.name if profile is not None else timer.profile_id,
                        "remaining_seconds": max(0, timer.expires_at - now),
                    }
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"timers": timers})
        if path == "/api/progressions":
            now = int(time.time())
            device_counters = {}
            scheduler_counters = {}
            if self.scheduler is not None:
                scheduler_counters = {
                    device.id: self.scheduler.device_traffic_bytes(device_id=device.id)
                    for device in self.repository.list_devices()
                }
            elif self.helper_client is not None:
                try:
                    device_counters = self.helper_client.traffic_counters().get("devices", {})
                except (HelperClientError, AttributeError, TypeError):
                    LOGGER.warning("could not read progression traffic counters")
            progressions = [
                _progression_payload(
                    progression,
                    repository=self.repository,
                    now=now,
                    traffic_bytes=scheduler_counters.get(
                        progression.device_id,
                        device_counters.get(progression.device_id, 0),
                    ),
                )
                for progression in self.repository.list_active_progressions(now=now)
            ]
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"progressions": progressions}
            )
        if path == "/api/data-limits":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "data_limits": [
                        _data_limit_payload(data_limit, repository=self.repository)
                        for data_limit in self.repository.list_data_limits()
                    ]
                }
            )
        if path == "/api/group-data-limits":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "group_data_limits": [
                        _group_data_limit_payload(data_limit, repository=self.repository)
                        for data_limit in self.repository.list_group_data_limits()
                    ]
                }
            )
        if path == "/api/service-quotas":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "service_quotas": [
                        _service_quota_payload(quota, repository=self.repository)
                        for quota in self.repository.list_service_quotas()
                    ]
                }
            )
        if path == "/api/time-quotas":
            now = int(time.time())
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "time_quotas": [
                        _time_quota_payload(quota, repository=self.repository, now=now)
                        for quota in self.repository.list_time_quotas()
                    ]
                }
            )
        if path == "/api/policies":
            return HTTPStatus.OK, "application/json", _json_bytes(
                _policies_payload(self.repository)
            )
        if path == "/api/router":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "model": "Vodafone TG3442DE",
                    "automatic_wifi_control": True,
                    "wifi_control": "tg3442de-local-web",
                    "message": (
                        "Router admin credentials are used for one authenticated request and are not saved. "
                        "Keep the Pi connected by Ethernet before changing router Wi-Fi."
                    ),
                }
            )
        if path == "/api/uplink":
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "uplink": None,
                        "message": "upstream connection information is unavailable in configuration-only mode",
                    }
                )
            try:
                uplink = self.helper_client.uplink_info()
            except HelperClientError:
                LOGGER.exception("could not inspect the upstream connection")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "could not inspect the upstream connection"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": True,
                    "uplink": uplink,
                    "router": {
                        "model": "Vodafone TG3442DE",
                        "settings_url": "http://192.168.0.1",
                        "wifi_control": "tg3442de-local-web",
                    },
                }
            )
        if path == "/api/connected-devices":
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "devices": [],
                        "message": "connected-client information is unavailable in configuration-only mode",
                    }
                )
            try:
                devices = self.helper_client.connected_devices()
            except HelperClientError:
                LOGGER.exception("could not inspect connected clients")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "could not inspect connected clients"}
                )
            assignments_by_address = {
                str(device.ipv4_address): device for device in self.repository.list_devices()
            }
            devices = [
                {
                    **device,
                    "assigned_device_id": (
                        assignments_by_address[device["ipv4_address"]].id
                        if device["ipv4_address"] in assignments_by_address
                        else None
                    ),
                    "assigned_device_name": (
                        assignments_by_address[device["ipv4_address"]].name
                        if device["ipv4_address"] in assignments_by_address
                        else None
                    ),
                    "assigned_profile_id": (
                        assignments_by_address[device["ipv4_address"]].profile_id
                        if device["ipv4_address"] in assignments_by_address
                        else None
                    ),
                }
                for device in devices
            ]
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"available": True, "devices": devices}
            )
        if path == "/api/observations":
            observations = self.repository.list_domain_observations()
            assignments_by_address = {
                str(device.ipv4_address): device for device in self.repository.list_devices()
            }
            grouped = {}
            for observation in observations:
                address = str(observation.ipv4_address)
                grouped.setdefault(address, []).append(_domain_observation_payload(observation))
            devices = [
                {
                    "ipv4_address": address,
                    "assigned_device_id": (
                        assignments_by_address[address].id
                        if address in assignments_by_address
                        else None
                    ),
                    "assigned_device_name": (
                        assignments_by_address[address].name
                        if address in assignments_by_address
                        else None
                    ),
                    "domains": domains,
                }
                for address, domains in sorted(grouped.items())
            ]
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": self.helper_client is not None,
                    "devices": devices,
                    "total_domains": sum(len(domains) for domains in grouped.values()),
                    "message": (
                        None
                        if self.helper_client is not None
                        else "traffic discovery is unavailable in configuration-only mode"
                    ),
                }
            )
        if path == "/api/plan":
            plan = (
                self.scheduler.build_plan(repository=self.repository)
                if self.scheduler is not None
                else compile_stored_plan(repository=self.repository)
            )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"plan": plan_to_payload(plan)}
            )
        if path == "/api/state":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"state": _runtime_state_payload(self.repository.get_runtime_state())}
            )
        if path == "/api/action-history":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"actions": list(self.repository.list_recent_actions())}
            )
        if path == "/api/backup":
            return HTTPStatus.OK, "application/vnd.sqlite3", self.repository.backup_bytes()
        if path == "/api/traffic":
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "message": "Live traffic needs hardware mode.",
                        "devices": {},
                    }
                )
            try:
                counters = self.helper_client.traffic_counters()
            except HelperClientError:
                LOGGER.exception("could not read live traffic counters")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "live traffic counters are unavailable"}
                )
            directions = counters.get("directions")
            device_counters = directions.get("devices") if isinstance(directions, dict) else None
            if not isinstance(device_counters, dict):
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "live traffic directions are unavailable"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": True,
                    "sampled_at": time.time(),
                    "devices": device_counters,
                }
            )
        if path == "/api/diagnostics":
            diagnostics = {
                "checked_at": int(time.time()),
                "runtime": _runtime_state_payload(self.repository.get_runtime_state()),
                "helper_available": self.helper_client is not None,
                "connected_clients": None,
                "traffic_counters": None,
                "warnings": [],
            }
            if self.helper_client is not None:
                try:
                    diagnostics["connected_clients"] = len(
                        self.helper_client.connected_devices()
                    )
                except (HelperClientError, AttributeError, TypeError):
                    diagnostics["warnings"].append("connected-client scan failed")
                try:
                    diagnostics["traffic_counters"] = self.helper_client.traffic_counters()
                except (HelperClientError, AttributeError, TypeError):
                    diagnostics["warnings"].append("traffic counters are unavailable")
            return HTTPStatus.OK, "application/json", _json_bytes(diagnostics)
        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def write_response_for(self, path: str, payload: Dict[str, Any]) -> Tuple[int, str, bytes]:
        """Handle one supported configuration or network action request."""

        if path == "/api/profiles":
            try:
                profile = _profile_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid profile configuration"}
                )
            self.repository.save_profile(profile)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"profile": _profile_payload(profile)}
            )

        if path == "/api/devices":
            try:
                device = _device_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid device configuration"}
                )
            self.repository.save_device(device)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"device": _device_payload(device)}
            )

        if path == "/api/groups":
            try:
                group = _device_group_from_payload(payload)
                for device_id in group.device_ids:
                    if self.repository.get_device(device_id) is None:
                        raise ValueError("device does not exist")
                if group.profile_id is not None and self.repository.get_profile(group.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_device_group(group)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid device group configuration"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"group": _device_group_payload(group, repository=self.repository)}
            )

        if path == "/api/groups/delete":
            try:
                group_id = payload["id"]
                if not isinstance(group_id, str) or not group_id:
                    raise ValueError("group id is invalid")
                if not self.repository.delete_device_group(group_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "device group does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid device group delete request"}
                )
            return self._apply_quota_change("group", {"id": group_id, "deleted": True})

        if path == "/api/schedules":
            try:
                schedule = _schedule_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid schedule configuration"}
                )
            self.repository.save_schedule(schedule)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"schedule": _schedule_payload(schedule)}
            )

        if path == "/api/timers":
            try:
                timer = _timer_from_payload(payload)
                if self.repository.get_device(timer.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(timer.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_timer(timer)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid timer configuration"}
                )
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "timer": _timer_payload(timer),
                        "live_applied": False,
                        "message": "timer saved; apply the configuration in hardware mode to start it",
                    }
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["timer"] = _timer_payload(timer)
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/timers/cancel":
            try:
                timer_id = payload["id"]
                if not isinstance(timer_id, str) or not timer_id:
                    raise ValueError("timer id is invalid")
                if not self.repository.delete_timer(timer_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "timer does not exist or is already inactive"}
                    )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid timer cancellation"}
                )
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {"timer_id": timer_id, "live_applied": False}
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["timer_id"] = timer_id
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/progressions":
            try:
                progression = _progression_from_payload(payload, repository=self.repository)
                if self.repository.get_device(progression.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(progression.start_profile_id) is None:
                    raise ValueError("start profile does not exist")
                if self.repository.get_profile(progression.end_profile_id) is None:
                    raise ValueError("end profile does not exist")
                self.repository.save_progression(progression)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid progression configuration"}
                )
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "progression": _progression_payload(
                            progression,
                            repository=self.repository,
                            now=int(time.time()),
                        ),
                        "live_applied": False,
                        "message": "progression saved; apply the configuration in hardware mode to start it",
                    }
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["progression"] = _progression_payload(
                progression,
                repository=self.repository,
                now=int(time.time()),
            )
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/progressions/stop":
            try:
                progression_id = payload["id"]
                if not isinstance(progression_id, str) or not progression_id:
                    raise ValueError("progression id is invalid")
                if not self.repository.delete_progression(progression_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "progression does not exist or is already inactive"}
                    )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid progression stop request"}
                )
            if self.helper_client is None:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {"progression_id": progression_id, "live_applied": False}
                )
            status, content_type, body = self._apply_after_change()
            response = json.loads(body.decode("utf-8"))
            response["progression_id"] = progression_id
            response.setdefault("live_applied", status == HTTPStatus.OK)
            return status, content_type, _json_bytes(response)

        if path == "/api/data-limits":
            try:
                data_limit = _data_limit_from_payload(payload, repository=self.repository)
                if self.repository.get_device(data_limit.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(data_limit.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_data_limit(data_limit)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid data limit configuration"}
                )
            return self._apply_quota_change(
                "data_limit",
                _data_limit_payload(data_limit, repository=self.repository),
            )

        if path == "/api/data-limits/reset":
            try:
                device_id = payload["device_id"]
                if not isinstance(device_id, str) or not device_id:
                    raise ValueError("device_id is invalid")
                data_limit = self.repository.get_data_limit(device_id)
                if data_limit is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "data limit does not exist"}
                    )
                self.repository.reset_data_limit_usage(
                    device_id,
                    counter_bytes=self._quota_counter("devices", device_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid data limit reset request"}
                )
            refreshed = self.repository.get_data_limit(device_id)
            return self._apply_quota_change(
                "data_limit",
                _data_limit_payload(refreshed, repository=self.repository),
            )

        if path == "/api/group-data-limits":
            try:
                data_limit = _group_data_limit_from_payload(
                    payload,
                    repository=self.repository,
                )
                if self.repository.get_device_group(data_limit.group_id) is None:
                    raise ValueError("device group does not exist")
                if self.repository.get_profile(data_limit.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_group_data_limit(data_limit)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid group data limit configuration"}
                )
            return self._apply_quota_change(
                "group_data_limit",
                _group_data_limit_payload(data_limit, repository=self.repository),
            )

        if path == "/api/group-data-limits/reset":
            try:
                group_id = payload["group_id"]
                if not isinstance(group_id, str) or not group_id:
                    raise ValueError("group_id is invalid")
                data_limit = self.repository.get_group_data_limit(group_id)
                if data_limit is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "group data limit does not exist"}
                    )
                self.repository.reset_group_data_limit_usage(
                    group_id,
                    counter_bytes=self._group_quota_counter(group_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid group data limit reset request"}
                )
            refreshed = self.repository.get_group_data_limit(group_id)
            return self._apply_quota_change(
                "group_data_limit",
                _group_data_limit_payload(refreshed, repository=self.repository),
            )

        if path == "/api/service-quotas":
            try:
                quota = _service_quota_from_payload(payload, repository=self.repository)
                if self.repository.get_device(quota.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(quota.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_service_quota(quota)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service quota configuration"}
                )
            return self._apply_quota_change(
                "service_quota",
                _service_quota_payload(quota, repository=self.repository),
            )

        if path == "/api/service-quotas/reset":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_service_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "service quota does not exist"}
                    )
                self.repository.reset_service_quota_usage(
                    quota_id,
                    counter_bytes=self._quota_counter("services", quota_id),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service quota reset request"}
                )
            refreshed = self.repository.get_service_quota(quota_id)
            return self._apply_quota_change(
                "service_quota",
                _service_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas":
            try:
                quota = _time_quota_from_payload(payload, repository=self.repository)
                if self.repository.get_device(quota.device_id) is None:
                    raise ValueError("device does not exist")
                if self.repository.get_profile(quota.profile_id) is None:
                    raise ValueError("profile does not exist")
                self.repository.save_time_quota(quota)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid time quota configuration"}
                )
            return self._apply_quota_change(
                "time_quota",
                _time_quota_payload(quota, repository=self.repository),
            )

        if path == "/api/time-quotas/start":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_time_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
                started_at = int(time.time())
                refreshed = self.repository.start_time_quota(
                    quota_id,
                    started_at=started_at,
                    expires_at=started_at + quota.window_duration_seconds,
                    counter_bytes=self._quota_counter(
                        "services", time_quota_counter_id(quota_id)
                    ),
                )
                if refreshed is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid time quota start request"}
                )
            return self._apply_quota_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas/stop":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                if not self.repository.stop_time_quota(quota_id):
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid time quota stop request"}
                )
            refreshed = self.repository.get_time_quota(quota_id)
            return self._apply_quota_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/time-quotas/reset":
            try:
                quota_id = payload["id"]
                if not isinstance(quota_id, str) or not quota_id:
                    raise ValueError("quota id is invalid")
                quota = self.repository.get_time_quota(quota_id)
                if quota is None:
                    return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                        {"error": "time quota does not exist"}
                    )
                self.repository.reset_time_quota_usage(
                    quota_id,
                    counter_bytes=self._quota_counter(
                        "services", time_quota_counter_id(quota_id)
                    ),
                    reset_at=int(time.time()),
                )
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid time quota reset request"}
                )
            refreshed = self.repository.get_time_quota(quota_id)
            return self._apply_quota_change(
                "time_quota",
                _time_quota_payload(refreshed, repository=self.repository),
            )

        if path == "/api/policies":
            try:
                mac_policy, website_policy = _policies_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid network policy configuration"}
                )
            self.repository.save_mac_policy(mac_policy)
            self.repository.save_website_policy(website_policy)
            return HTTPStatus.OK, "application/json", _json_bytes(
                _policies_payload(self.repository)
            )

        if path == "/api/access-point":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            try:
                result = self.helper_client.configure_access_point(
                    ssid=payload["ssid"],
                    password=payload["password"],
                )
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid access point settings"}
                )
            except HelperClientError:
                LOGGER.exception("access point configuration failed")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "access point configuration failed"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        if path == "/api/router/sync-ap":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            admin_password = payload.get("admin_password")
            if not isinstance(admin_password, str) or not admin_password:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router admin password is required"}
                )
            try:
                result = self.helper_client.sync_access_point_from_router(
                    admin_password=admin_password
                )
            except HelperClientError:
                LOGGER.exception("router Wi-Fi import failed")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "router Wi-Fi import failed"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        if path == "/api/router/wifi":
            if self.helper_client is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                    {"error": "network helper is not configured"}
                )
            admin_password = payload.get("admin_password")
            enabled = payload.get("enabled")
            if not isinstance(admin_password, str) or not admin_password:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router admin password is required"}
                )
            if not isinstance(enabled, bool):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "router Wi-Fi state is invalid"}
                )
            if not enabled and payload.get("confirm_disable") is not True:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "turning off router Wi-Fi requires confirmation"}
                )
            try:
                result = self.helper_client.set_router_wifi(
                    admin_password=admin_password,
                    enabled=enabled,
                )
            except HelperClientError:
                LOGGER.exception("router Wi-Fi change failed")
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": "router Wi-Fi change failed"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes({"result": result})

        if path == "/api/connected-devices/apply-profile":
            return self._apply_profile_to_connected_device(payload)

        if path == "/api/observations/scan":
            return self._scan_dns_observations()

        if path == "/api/observations/add-to-profile":
            return self._add_observation_to_profile(payload)

        if path == "/api/observations/add-global-block":
            return self._add_observation_to_global_block(payload)

        if path == "/api/apply":
            return self._apply_configuration()

        if path == "/api/reset":
            return self._reset_network()

        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def _scan_dns_observations(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        try:
            raw_observations = self.helper_client.observe_dns_queries()
            observed_at = int(time.time())
            observations = [
                DomainObservation(
                    ipv4_address=observation["ipv4_address"],
                    domain=observation["domain"],
                    query_count=observation["query_count"],
                    first_seen=observed_at,
                    last_seen=observed_at,
                )
                for observation in raw_observations
            ]
            self.repository.save_domain_observations(observations, now=observed_at)
        except (HelperClientError, KeyError, TypeError, ValueError, StorageError):
            LOGGER.exception("could not scan DNS observations")
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "could not scan traffic observations"}
            )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {
                "captured_observations": len(observations),
                "observations": [
                    {
                        "ipv4_address": str(observation.ipv4_address),
                        **_domain_observation_payload(observation),
                    }
                    for observation in observations
                ],
            }
        )

    def _add_observation_to_profile(
        self, payload: Dict[str, Any]
    ) -> Tuple[int, str, bytes]:
        try:
            profile_id = payload["profile_id"]
            domain = normalize_domain(payload["domain"])
            profile = self.repository.get_profile(profile_id)
            if profile is None:
                raise ValueError("profile does not exist")
            updated = replace(
                profile,
                website_mode="allow_only",
                website_domains=tuple(dict.fromkeys(profile.website_domains + (domain,))),
            )
            self.repository.save_profile(updated)
        except (KeyError, TypeError, ValueError, StorageError):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "could not add domain to profile"}
            )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"profile": _profile_payload(updated)}
        )

    def _add_observation_to_global_block(
        self, payload: Dict[str, Any]
    ) -> Tuple[int, str, bytes]:
        try:
            domain = normalize_domain(payload["domain"])
            policy = self.repository.get_website_policy()
            updated = replace(
                policy,
                blocked_domains=tuple(dict.fromkeys(policy.blocked_domains + (domain,))),
            )
            self.repository.save_website_policy(updated)
        except (KeyError, TypeError, ValueError, StorageError):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "could not add domain to global block list"}
            )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"policies": _policies_payload(self.repository)}
        )

    def _apply_profile_to_connected_device(
        self, payload: Dict[str, Any]
    ) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        try:
            device = _connected_device_from_payload(payload, self.repository)
        except (KeyError, TypeError, ValueError):
            return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                {"error": "invalid connected-device profile assignment"}
            )
        self.repository.save_device(device)
        status, content_type, body = self._apply_configuration()
        response = json.loads(body.decode("utf-8"))
        response["device"] = _device_payload(device)
        return status, content_type, _json_bytes(response)

    def _quota_counter(self, kind: str, identifier: str) -> Optional[int]:
        """Read one current helper counter for a manual quota reset."""

        if self.helper_client is None:
            return None
        traffic_counters = getattr(self.helper_client, "traffic_counters", None)
        if traffic_counters is None:
            return None
        try:
            counters = traffic_counters()
        except HelperClientError:
            LOGGER.warning("could not read quota counter before reset", exc_info=True)
            return None
        values = counters.get(kind, {})
        value = values.get(identifier) if isinstance(values, dict) else None
        return value if isinstance(value, int) and value >= 0 else None

    def _group_quota_counter(self, group_id: str) -> Optional[int]:
        """Return the current aggregate device counter for one group."""

        if self.helper_client is None:
            return None
        group = self.repository.get_device_group(group_id)
        if group is None:
            return None
        try:
            counters = self.helper_client.traffic_counters().get("devices", {})
        except HelperClientError:
            LOGGER.warning("could not read group quota counter before reset", exc_info=True)
            return None
        if not isinstance(counters, dict) or any(
            device_id not in counters for device_id in group.device_ids
        ):
            return None
        values = [counters[device_id] for device_id in group.device_ids]
        return sum(values) if all(isinstance(value, int) and value >= 0 for value in values) else None

    def _apply_quota_change(
        self,
        key: str,
        value: Dict[str, Any],
    ) -> Tuple[int, str, bytes]:
        """Return a saved quota and apply it immediately when hardware is available."""

        if self.helper_client is None:
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    key: value,
                    "live_applied": False,
                    "message": "quota saved; apply the configuration in hardware mode to start it",
                }
            )
        status, content_type, body = self._apply_after_change()
        response = json.loads(body.decode("utf-8"))
        response[key] = value
        response.setdefault("live_applied", status == HTTPStatus.OK)
        return status, content_type, _json_bytes(response)

    def _apply_after_change(self) -> Tuple[int, str, bytes]:
        """Re-apply the plan for a saved change unless the network was reset."""

        if self.repository.get_runtime_state().last_action == "reset":
            # Only an explicit Apply may undo a Reset.
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "live_applied": False,
                    "message": "saved; the network is reset, so apply the configuration to use it",
                }
            )
        return self._apply_configuration()

    def _apply_configuration(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        if self.scheduler is None:
            return self._apply_current_plan()
        with self.scheduler.exclusive():
            return self._apply_current_plan()

    def _apply_current_plan(self) -> Tuple[int, str, bytes]:
        try:
            plan = (
                self.scheduler.build_plan(repository=self.repository)
                if self.scheduler is not None
                else compile_stored_plan(repository=self.repository)
            )
        except (PlanError, StorageError):
            return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                {"error": "stored configuration cannot be applied"}
            )
        self.repository.update_runtime_state(
            observed_state="applying",
            last_action="apply",
            last_result=None,
            last_error=None,
        )
        try:
            result = self.helper_client.apply(plan=plan)
        except HelperClientError as error:
            LOGGER.error("network helper apply failed: %s", error)
            state = self.repository.update_runtime_state(
                observed_state="error",
                last_action="apply",
                last_result="failure",
                last_error="network helper apply failed",
            )
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "network helper apply failed", "state": _runtime_state_payload(state)}
            )
        state = self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )
        if self.scheduler is not None:
            self.scheduler.activate(applied_payload=plan_to_payload(plan))
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"result": result, "state": _runtime_state_payload(state)}
        )

    def _reset_network(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        if self.scheduler is not None:
            self.scheduler.pause()
        self.repository.update_runtime_state(
            observed_state="resetting",
            last_action="reset",
            last_result=None,
            last_error=None,
        )
        try:
            result = self.helper_client.reset()
        except HelperClientError as error:
            LOGGER.error("network helper reset failed: %s", error)
            state = self.repository.update_runtime_state(
                observed_state="error",
                last_action="reset",
                last_result="failure",
                last_error="network helper reset failed",
            )
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "network helper reset failed", "state": _runtime_state_payload(state)}
            )
        state = self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"result": result, "state": _runtime_state_payload(state)}
        )

    def is_authorized(self, authorization_header: Optional[str]) -> bool:
        """Return whether an Authorization header matches the configured admin token."""

        return _authorization_matches(
            expected_token=self.admin_token,
            authorization_header=authorization_header,
        )

    def recover_saved_network_state(self) -> None:
        """Restore the last explicit apply or reset after the service restarts."""

        if self.helper_client is None:
            return
        state = self.repository.get_runtime_state()
        if state.last_action == "reset":
            status, _, _ = self._reset_network()
        elif state.last_action in {"apply", "schedule"}:
            status, _, _ = self._apply_configuration()
        else:
            return
        if status != HTTPStatus.OK:
            LOGGER.error("network state recovery did not complete successfully")


class ControlRequestHandler(BaseHTTPRequestHandler):
    """Handle the control-plane routes."""

    server: ControlServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in PROTECTED_READ_PATHS:
            if self.server.admin_token is None:
                self._send_response(
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    content_type="application/json",
                    body=_json_bytes({"error": "protected reads are disabled"}),
                )
                return
            if not self.server.is_authorized(self.headers.get("Authorization")):
                self._send_response(
                    status=HTTPStatus.UNAUTHORIZED,
                    content_type="application/json",
                    body=_json_bytes({"error": "valid bearer authentication is required"}),
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return
        try:
            status, content_type, body = self.server.response_for(path)
        except (PlanError, StorageError):
            LOGGER.exception("could not build control-plane response for %s", path)
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content_type = "application/json"
            body = _json_bytes({"error": "stored configuration could not be read"})
        headers = (
            {"Content-Disposition": 'attachment; filename="jittersim-backup.db"'}
            if path == "/api/backup"
            else None
        )
        self._send_response(status=status, content_type=content_type, body=body, headers=headers)

    def do_POST(self) -> None:
        self._handle_write()

    def do_PUT(self) -> None:
        self._handle_write()

    def do_DELETE(self) -> None:
        self._send_response(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            content_type="application/json",
            body=_json_bytes({"error": "delete actions are not enabled"}),
        )

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)

    def _handle_write(self) -> None:
        path = urlsplit(self.path).path
        if path not in WRITE_PATHS:
            self._send_response(
                status=HTTPStatus.NOT_FOUND,
                content_type="application/json",
                body=_json_bytes({"error": "not found"}),
            )
            return
        if self.server.admin_token is None:
            self._send_response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                content_type="application/json",
                body=_json_bytes({"error": "state-changing actions are disabled"}),
            )
            return
        if not self.server.is_authorized(self.headers.get("Authorization")):
            self._send_response(
                status=HTTPStatus.UNAUTHORIZED,
                content_type="application/json",
                body=_json_bytes({"error": "valid bearer authentication is required"}),
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        try:
            payload = self._read_json_body(required=path not in ACTION_PATHS)
            status, content_type, body = self.server.write_response_for(path, payload)
        except RequestError as error:
            status = HTTPStatus.BAD_REQUEST
            content_type = "application/json"
            body = _json_bytes({"error": str(error)})
        except StorageError:
            LOGGER.exception("could not persist control-plane request for %s", path)
            status = HTTPStatus.CONFLICT
            content_type = "application/json"
            body = _json_bytes({"error": "configuration could not be saved"})
        try:
            self.server.repository.record_action(action=path, status_code=int(status))
        except StorageError:
            LOGGER.exception("could not record action history for %s", path)
        self._send_response(status=status, content_type=content_type, body=body)

    def _read_json_body(self, *, required: bool = True) -> Dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            if not required:
                return {}
            raise RequestError("request body is required")
        try:
            length = int(length_header)
        except ValueError as error:
            raise RequestError("request body length is invalid") from error
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise RequestError("request body is too large")
        if length == 0 and not required:
            return {}
        raw_body = self.rfile.read(length)
        if len(raw_body) != length:
            raise RequestError("request body is incomplete")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestError("request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise RequestError("request body must contain a JSON object")
        return payload

    def _send_response(
        self,
        *,
        status: int,
        content_type: str,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class RequestError(ValueError):
    """Raised when an HTTP request body cannot be safely decoded."""


def _impairment_from_payload(payload: Any) -> Impairment:
    if payload is None:
        return Impairment()
    if not isinstance(payload, dict):
        raise ValueError("impairment must be an object")
    impairment_data = dict(payload)
    for field_name in (
        "delay_randomness",
        "jitter_randomness",
        "loss_randomness",
        "bandwidth_randomness",
        "reorder_randomness",
        "duplicate_randomness",
        "corrupt_randomness",
    ):
        randomness_data = impairment_data.get(field_name)
        if isinstance(randomness_data, dict):
            impairment_data[field_name] = Randomness(**randomness_data)
    return Impairment(**impairment_data)


def _randomness_from_payload(payload: Any) -> Randomness:
    if payload is None:
        return Randomness()
    if not isinstance(payload, dict):
        raise ValueError("randomness must be an object")
    return Randomness(**payload)


def _profile_from_payload(payload: Dict[str, Any]) -> Profile:
    return Profile(
        id=payload["id"],
        name=payload["name"],
        upload=_impairment_from_payload(payload.get("upload")),
        download=_impairment_from_payload(payload.get("download")),
        random_seed=payload.get("random_seed"),
        reroll_interval_seconds=payload.get("reroll_interval_seconds", 0),
        reroll_interval_randomness=_randomness_from_payload(
            payload.get("reroll_interval_randomness")
        ),
        outage_probability=payload.get("outage_probability", 0),
        outage_duration_seconds=payload.get("outage_duration_seconds", 0),
        outage_duration_randomness=_randomness_from_payload(
            payload.get("outage_duration_randomness")
        ),
        outage_interval_seconds=payload.get("outage_interval_seconds", 0),
        outage_interval_randomness=_randomness_from_payload(
            payload.get("outage_interval_randomness")
        ),
        website_mode=payload.get("website_mode", "unrestricted"),
        website_domains=tuple(payload.get("website_domains", ())),
        website_randomness=_randomness_from_payload(payload.get("website_randomness")),
    )


def _device_from_payload(payload: Dict[str, Any]) -> Device:
    return Device(
        id=payload["id"],
        name=payload["name"],
        ipv4_address=payload["ipv4_address"],
        profile_id=payload["profile_id"],
        mac_address=payload.get("mac_address"),
    )


def _schedule_from_payload(payload: Dict[str, Any]) -> Schedule:
    return Schedule(
        id=payload["id"],
        device_id=payload["device_id"],
        profile_id=payload["profile_id"],
        days_of_week=tuple(payload["days_of_week"]),
        start_minute=payload["start_minute"],
        end_minute=payload["end_minute"],
        enabled=payload.get("enabled", True),
    )


def _timer_from_payload(payload: Dict[str, Any]) -> Timer:
    duration_minutes = payload["duration_minutes"]
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise ValueError("duration_minutes must be an integer")
    if duration_minutes < 1 or duration_minutes > 10_080:
        raise ValueError("duration_minutes must be between 1 and 10080")
    started_at = int(time.time())
    return Timer(
        id=payload.get("id", f"timer-{time.time_ns()}"),
        device_id=payload["device_id"],
        profile_id=payload["profile_id"],
        started_at=started_at,
        expires_at=started_at + duration_minutes * 60,
    )


def _quota_schedule_from_payload(payload: Dict[str, Any]) -> Tuple[str, int, Tuple[int, ...]]:
    """Read the shared manual, interval or daily reset settings."""

    reset_mode = payload.get("reset_mode", "manual")
    reset_interval_seconds = payload.get("reset_interval_seconds", 0)
    reset_times = tuple(payload.get("reset_times", ()))
    return reset_mode, reset_interval_seconds, reset_times


def _data_limit_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> DataLimit:
    """Build a device quota while retaining usage when it is edited."""

    device_id = payload["device_id"]
    existing = repository.get_data_limit(device_id)
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return DataLimit(
        device_id=device_id,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _device_group_from_payload(payload: Dict[str, Any]) -> DeviceGroup:
    """Build a device group from selected device IDs."""

    return DeviceGroup(
        id=payload.get("id", f"group-{time.time_ns()}"),
        name=payload["name"],
        device_ids=tuple(payload["device_ids"]),
        profile_id=payload.get("profile_id") or None,
    )


def _group_data_limit_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> GroupDataLimit:
    """Build a shared group quota while retaining usage when it is edited."""

    group_id = payload["group_id"]
    existing = repository.get_group_data_limit(group_id)
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return GroupDataLimit(
        group_id=group_id,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _service_quota_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> ServiceQuota:
    """Build a service quota from selected observed DNS domains."""

    quota_id = payload.get("id", f"service-{time.time_ns()}")
    existing = repository.get_service_quota(quota_id)
    normalized_domains = tuple(normalize_domain(domain) for domain in payload["domains"])
    device = repository.get_device(payload["device_id"])
    if device is not None:
        observed_domains = {
            observation.domain
            for observation in repository.list_domain_observations()
            if str(observation.ipv4_address) == str(device.ipv4_address)
        }
        previous_domains = set(existing.domains) if existing is not None else set()
        if any(
            domain not in observed_domains and domain not in previous_domains
            for domain in normalized_domains
        ):
            raise ValueError("service quota domains must come from DNS observations")
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    return ServiceQuota(
        id=quota_id,
        device_id=payload["device_id"],
        name=payload["name"],
        domains=normalized_domains,
        profile_id=payload["profile_id"],
        limit_bytes=payload["limit_bytes"],
        weight_percent=payload.get(
            "weight_percent",
            existing.weight_percent if existing is not None else 100,
        ),
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_bytes=existing.used_bytes if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        enabled=payload.get("enabled", True),
    )


def _time_quota_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> TimeQuota:
    """Build a timed service quota from selected observed DNS domains."""

    quota_id = payload.get("id", f"time-{time.time_ns()}")
    existing = repository.get_time_quota(quota_id)
    normalized_domains = tuple(normalize_domain(domain) for domain in payload["domains"])
    device = repository.get_device(payload["device_id"])
    if device is not None:
        observed_domains = {
            observation.domain
            for observation in repository.list_domain_observations()
            if str(observation.ipv4_address) == str(device.ipv4_address)
        }
        previous_domains = set(existing.domains) if existing is not None else set()
        if any(
            domain not in observed_domains and domain not in previous_domains
            for domain in normalized_domains
        ):
            raise ValueError("time quota domains must come from DNS observations")
    reset_mode, reset_interval_seconds, reset_times = _quota_schedule_from_payload(payload)
    window_duration_seconds = payload["window_duration_seconds"]
    started_at = existing.started_at if existing is not None else 0
    expires_at = existing.expires_at if existing is not None else 0
    if started_at and expires_at - started_at > window_duration_seconds:
        raise ValueError("stop the timer before shortening its window")
    return TimeQuota(
        id=quota_id,
        device_id=payload["device_id"],
        name=payload["name"],
        domains=normalized_domains,
        profile_id=payload["profile_id"],
        limit_seconds=payload["limit_seconds"],
        window_duration_seconds=window_duration_seconds,
        reset_mode=reset_mode,
        reset_interval_seconds=reset_interval_seconds,
        reset_times=reset_times,
        used_seconds=existing.used_seconds if existing is not None else 0,
        counter_bytes=existing.counter_bytes if existing is not None else 0,
        last_sample_at=existing.last_sample_at if existing is not None else 0,
        last_reset_at=existing.last_reset_at if existing is not None else int(time.time()),
        started_at=started_at,
        expires_at=expires_at,
        enabled=payload.get("enabled", True),
    )


def _progression_from_payload(
    payload: Dict[str, Any],
    *,
    repository: ConfigRepository,
) -> Progression:
    duration_minutes = payload["duration_minutes"]
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise ValueError("duration_minutes must be an integer")
    if duration_minutes < 1 or duration_minutes > 10_080:
        raise ValueError("duration_minutes must be between 1 and 10080")
    mode = payload.get("mode", "time")
    traffic_target_queries = payload.get("traffic_target_queries", 0)
    traffic_target_bytes = payload.get("traffic_target_bytes", 0)
    device = repository.get_device(payload["device_id"])
    if device is None:
        raise ValueError("device does not exist")
    return Progression(
        id=payload.get("id", f"progression-{time.time_ns()}"),
        device_id=payload["device_id"],
        start_profile_id=payload["start_profile_id"],
        end_profile_id=payload["end_profile_id"],
        started_at=int(time.time()),
        duration_seconds=duration_minutes * 60,
        mode=mode,
        traffic_target_queries=traffic_target_queries,
        traffic_baseline_queries=repository.total_domain_queries(str(device.ipv4_address)),
        traffic_target_bytes=traffic_target_bytes,
        website_domains=tuple(payload.get("website_domains", ())),
    )


def _policies_from_payload(payload: Dict[str, Any]) -> Tuple[MacPolicy, WebsitePolicy]:
    mac_payload = payload.get("mac", {})
    website_payload = payload.get("websites", {})
    if not isinstance(mac_payload, dict) or not isinstance(website_payload, dict):
        raise ValueError("policy sections must be objects")
    return (
        MacPolicy(
            mode=mac_payload.get("mode", "disabled"),
            allowlist=tuple(mac_payload.get("allowlist", ())),
            denylist=tuple(mac_payload.get("denylist", ())),
        ),
        WebsitePolicy(
            mode=website_payload.get("mode", "unrestricted"),
            allowed_domains=tuple(website_payload.get("allowed_domains", ())),
            blocked_domains=tuple(website_payload.get("blocked_domains", ())),
        ),
    )


def _connected_device_from_payload(
    payload: Dict[str, Any], repository: ConfigRepository
) -> Device:
    """Build an assignment for a visible client while preserving existing names and IDs."""

    ipv4_address = payload["ipv4_address"]
    profile_id = payload["profile_id"]
    mac_address = payload.get("mac_address")
    if repository.get_profile(profile_id) is None:
        raise ValueError("profile does not exist")

    address_only = Device(
        id="connected-client",
        name="Connected client",
        ipv4_address=ipv4_address,
        profile_id=profile_id,
    )
    existing_by_address = repository.get_device_by_ipv4_address(str(address_only.ipv4_address))
    if existing_by_address is not None:
        return Device(
            id=existing_by_address.id,
            name=existing_by_address.name,
            ipv4_address=address_only.ipv4_address,
            profile_id=profile_id,
            mac_address=mac_address or existing_by_address.mac_address,
        )

    generated_id = "client-" + str(address_only.ipv4_address).replace(".", "-")
    existing_generated_id = repository.get_device(generated_id)
    if existing_generated_id is not None:
        raise ValueError("generated connected-device ID is already assigned")
    return Device(
        id=generated_id,
        name=f"Client {address_only.ipv4_address}",
        ipv4_address=address_only.ipv4_address,
        profile_id=profile_id,
        mac_address=mac_address,
    )


def _authorization_matches(
    *, expected_token: Optional[str], authorization_header: Optional[str]
) -> bool:
    if expected_token is None or authorization_header is None:
        return False
    scheme, separator, token = authorization_header.partition(" ")
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and hmac.compare_digest(token, expected_token)
    )


def run_server(
    *,
    database_path: DatabasePath,
    host: str = "127.0.0.1",
    port: int = 8080,
    admin_token: Optional[str] = None,
    helper_socket_path: Optional[Path] = None,
    helper_timeout_seconds: float = 8.0,
    tls_certfile: Optional[Path] = None,
    tls_keyfile: Optional[Path] = None,
) -> None:
    """Run the dashboard until the process receives Ctrl-C."""

    helper_client = (
        None
        if helper_socket_path is None
        else HelperClient(
            socket_path=helper_socket_path,
            timeout_seconds=helper_timeout_seconds,
        )
    )
    with ConfigRepository(database_path) as repository:
        scheduler = RuntimeScheduler(
            database_path=database_path,
            helper_client=helper_client,
        )
        with ControlServer(
            (host, port),
            repository,
            admin_token=admin_token,
            helper_client=helper_client,
            scheduler=scheduler,
            tls_certfile=tls_certfile,
            tls_keyfile=tls_keyfile,
        ) as server:
            LOGGER.info("jittersim dashboard listening on %s:%s", host, port)
            try:
                server.recover_saved_network_state()
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                scheduler.stop()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim dashboard.")
    parser.add_argument("--database", type=Path, default=Path("jittersim.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--helper-socket", type=Path)
    parser.add_argument("--helper-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse server options and start the dashboard."""

    arguments = _build_parser().parse_args(argv)
    run_server(
        database_path=arguments.database,
        host=arguments.host,
        port=arguments.port,
        admin_token=os.environ.get("JITTERSIM_ADMIN_TOKEN"),
        helper_socket_path=arguments.helper_socket,
        helper_timeout_seconds=arguments.helper_timeout_seconds,
        tls_certfile=arguments.tls_cert,
        tls_keyfile=arguments.tls_key,
    )
    return 0
