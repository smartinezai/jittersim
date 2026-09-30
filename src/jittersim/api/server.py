"""Small HTTP server for the jittersim control plane."""

import argparse
import hmac
import json
import logging
import os
import ssl
import time
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import IPv4Network
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

from jittersim.config.device_labels import (
    DeviceLabelError,
    guess_device_type,
    normalize_device_display_name,
    normalize_device_mac,
    normalize_device_type,
)
from jittersim.config.exemptions import UnaffectedDevice, normalize_exempt_mac
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
    ServiceAssignment,
    ServiceQuota,
    TimeQuota,
    Timer,
    WebsitePolicy,
    normalize_domain,
    time_quota_counter_id,
)
from jittersim.config.storage import (
    ConfigRepository,
    DatabasePath,
    StorageError,
    TrafficRateSettingsError,
)
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.compiler import MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN, PlanError
from jittersim.engine.eligibility import eligible_clients, station_snapshot
from jittersim.engine.progression import progression_factor
from jittersim.engine.runtime import RuntimeScheduler
from jittersim.engine.schedules import active_schedule
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.networking.router import safe_router_import_message

LOGGER = logging.getLogger(__name__)
MAX_JSON_BODY_BYTES = 64 * 1024
WRITE_PATHS = frozenset(
    (
        "/api/profiles",
        "/api/devices",
        "/api/unaffected-devices",
        "/api/unaffected-devices/delete",
        "/api/connected-devices/group",
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
        "/api/service-assignments",
        "/api/service-assignments/delete",
        "/api/time-quotas",
        "/api/action-history",
        "/api/diagnostics",
        "/api/backup",
        "/api/time-quotas/reset",
        "/api/time-quotas/start",
        "/api/time-quotas/stop",
        "/api/policies",
        "/api/access-point",
        "/api/router/sync-ap",
        "/api/router/wifi",
        "/api/connected-devices/apply-profile",
        "/api/connected-device-labels",
        "/api/connected-device-labels/delete",
        "/api/traffic-settings",
        "/api/observations/scan",
        "/api/observations/add-to-profile",
        "/api/observations/add-global-block",
        "/api/apply",
        "/api/reset",
    )
)
ACTION_PATHS = frozenset(("/api/apply", "/api/reset", "/api/observations/scan"))
PUBLIC_READ_PATHS = frozenset(("/", "/api/health"))


DASHBOARD_HTML = """<!doctype html>
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
      min-width: 0;
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
    button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible, .help-icon:focus-visible {
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
      grid-template-columns: auto minmax(0, 1fr) auto;
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
    .connected-controls { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
    .connected-controls select { min-width: 0; width: 12rem; max-width: 100%; }
    .connected-controls button { margin-top: 0; white-space: nowrap; }
    .connected-label-editor { margin-top: 0.4rem; color: #a8bdd4; font-size: 0.78rem; }
    .connected-label-editor summary { cursor: pointer; }
    .connected-label-form {
      display: flex;
      flex-wrap: wrap;
      align-items: end;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }
    .connected-label-form label { min-width: 9rem; max-width: 14rem; flex: 1; }
    .connected-label-form input { padding: 0.45rem 0.5rem; }
    .connected-label-form button { margin: 0; padding: 0.45rem 0.6rem; }
    .saved-device-labels {
      border-top: 1px solid #2b435f;
      margin-top: 0.75rem;
      padding-top: 0.75rem;
    }
    .saved-device-labels h3 { margin-top: 0; }
    .saved-device-label-row { grid-template-columns: minmax(0, 1fr) auto auto; }
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
    .traffic-rate-form { display: flex; flex-wrap: wrap; align-items: end; gap: 0.75rem; margin-top: 0.85rem; padding: 0.75rem; border: 1px solid #2b435f; border-radius: 0.6rem; background: #0d1b2d; }
    .traffic-rate-form label { min-width: 10rem; max-width: 14rem; flex: 1; }
    .traffic-rate-form input { width: 100%; }
    .traffic-rate-form button { margin: 0; }
    .traffic-rate-help, .traffic-profile-status { margin: 0; color: #91a8c1; font-size: 0.78rem; }
    .traffic-rate-help { flex-basis: 100%; }
    .traffic-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; margin-top: 1rem; }
    .traffic-metric { min-width: 0; padding: 0.75rem; border: 1px solid #2b435f; border-radius: 0.6rem; background: #0d1b2d; }
    .traffic-metric span { display: block; color: #91a8c1; font-size: 0.76rem; }
    .traffic-metric strong { display: block; margin-top: 0.25rem; color: #edf5ff; font-size: 1.05rem; overflow-wrap: anywhere; }
    .traffic-chart-wrap { margin-top: 1rem; padding: 0.65rem; border: 1px solid #2b435f; border-radius: 0.65rem; background: #0b1727; }
    #traffic-chart { display: block; width: 100%; height: 16rem; }
    .traffic-legend { display: flex; flex-wrap: wrap; gap: 0.8rem 1rem; margin-top: 0.65rem; color: #91a8c1; font-size: 0.78rem; }
    .traffic-legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
    .traffic-key { width: 0.75rem; height: 0.2rem; border-radius: 1rem; background: #70b7ff; }
    .traffic-key.download { background: #70d7a9; }
    .traffic-key.isp-upload { height: 0; border-top: 2px dashed #75a9ff; background: transparent; }
    .traffic-key.isp-download { height: 0; border-top: 2px dashed #75d7c2; background: transparent; }
    .traffic-key.profile-upload { height: 0; border-top: 2px dotted #ffa76c; background: transparent; }
    .traffic-key.profile-download { height: 0; border-top: 2px dotted #edc36a; background: transparent; }
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
    .field-label { position: relative; display: inline-flex; align-items: center; gap: 0.35rem; }
    .help-icon {
      position: static;
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
      display: none;
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
      display: block;
      opacity: 1;
      visibility: visible;
    }
    .help-icon.token-help::after { white-space: pre-line; }
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
    .randomness-title { position: relative; display: flex; align-items: center; gap: 0.35rem; color: #edf5ff; font-weight: 700; }
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
    .analytics-card { grid-column: 1 / -1; }
    .analytics-note, .analytics-status { margin: 0.75rem 0 0; color: #91a8c1; font-size: 0.82rem; }
    .analytics-status.error { color: #ffb0b0; }
    .analytics-controls { display: grid; grid-template-columns: repeat(2, minmax(12rem, 18rem)); gap: 0.75rem; margin-top: 1rem; }
    .analytics-table-wrap { margin-top: 1rem; overflow-x: auto; }
    .analytics-table { width: 100%; min-width: 34rem; border-collapse: collapse; text-align: left; }
    .analytics-table th, .analytics-table td { padding: 0.6rem 0.7rem; border-bottom: 1px solid #2b435f; }
    .analytics-table th { color: #a8c7e8; font-size: 0.78rem; font-weight: 700; }
    .analytics-table td { color: #d7e4f2; font-size: 0.84rem; }
    .analytics-table td:nth-child(n + 3) { text-align: right; font-variant-numeric: tabular-nums; }
    .analytics-domain { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
    @media (max-width: 700px) {
      body { padding-top: 1rem; }
      .page-header, .action-bar { display: block; }
      .action-bar { position: static; }
      .help-icon::after { position: fixed; top: auto; bottom: 1rem; left: 1rem; width: calc(100vw - 2rem); }
      .badge { display: inline-block; margin-top: 1rem; }
      .action-buttons { flex-wrap: wrap; }
      .action-buttons button { margin-top: 0.9rem; }
      .content-grid, .data-grid { grid-template-columns: 1fr; }
      .policy-grid { grid-template-columns: 1fr; }
      .uplink-grid { grid-template-columns: 1fr; }
      .connected-row { grid-template-columns: auto minmax(0, 1fr) auto; }
      .connected-controls { grid-column: 1 / -1; }
      .connected-controls { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
      .connected-controls select { width: 100%; }
      .connected-controls button:last-child { grid-column: 1 / -1; }
      .section-heading { flex-wrap: wrap; }
      .connected-label-form label { max-width: none; }
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
      .analytics-controls { grid-template-columns: 1fr; }
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
      <p id="runtime-summary" class="runtime-summary unknown">Check the admin token to view the network state.</p>
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
        <p>Devices on the Pi’s Wi-Fi. Devices on the router’s Wi-Fi are not listed here.</p>
      </div>
      <span id="connected-count" class="count-badge">Check token</span>
    </div>
    <div id="connected-devices" class="connected-list" aria-live="polite">Enter the admin token and choose Check token to view connected devices.</div>
  </section>
  <section class="card connected-card" aria-labelledby="unaffected-title">
    <div class="section-heading">
      <div>
        <h2 id="unaffected-title">Unaffected by profiles</h2>
        <p>Saved protection stays active when a device disconnects or changes IP. Global access rules still apply.</p>
      </div>
    </div>
    <div id="unaffected-devices" class="connected-list" aria-live="polite">Check the admin token to view saved devices.</div>
    <details>
      <summary>Add an offline device</summary>
      <form id="unaffected-form">
        <div class="fields">
          <label>Wi-Fi MAC address <input id="unaffected-mac" required placeholder="aa:bb:cc:dd:ee:ff"></label>
          <label>Name <input id="unaffected-name" maxlength="64" placeholder="Optional name"></label>
        </div>
        <button type="submit">Save protection</button>
      </form>
    </details>
    <p class="form-note">Use “Make unaffected” beside a connected device to add it without typing an address. If a device changes its private Wi-Fi MAC, add its new address.</p>
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
    <form id="traffic-rate-form" class="traffic-rate-form">
      <label>ISP download maximum (Mbit/s)
        <input id="traffic-isp-download" type="number" min="0.1" max="1000" step="0.1" value="100.0">
      </label>
      <label>ISP upload maximum (Mbit/s)
        <input id="traffic-isp-upload" type="number" min="0.1" max="1000" step="0.1" placeholder="Unknown">
      </label>
      <button class="primary" type="submit">Save speed references</button>
      <p id="traffic-rate-status" class="traffic-rate-help" role="status" aria-live="polite"></p>
      <p class="traffic-rate-help">These reference lines do not limit traffic. The ISP plan maximum is shared with other home devices.</p>
    </form>
    <div class="traffic-metrics">
      <div class="traffic-metric"><span>Upload rate</span><strong id="traffic-upload-rate">—</strong></div>
      <div class="traffic-metric"><span>Download rate</span><strong id="traffic-download-rate">—</strong></div>
      <div class="traffic-metric"><span>Upload packet rate</span><strong id="traffic-upload-packets">—</strong></div>
      <div class="traffic-metric"><span>Download packet rate</span><strong id="traffic-download-packets">—</strong></div>
    </div>
    <div class="traffic-chart-wrap">
      <canvas id="traffic-chart" width="900" height="320" role="img" aria-label="Live upload and download rates at JitterSim with configured ISP and device queue limits over the last 60 seconds">Live traffic chart. Check the admin token to begin sampling.</canvas>
      <div class="traffic-legend" aria-label="Graph legend">
        <span><i class="traffic-key"></i>Upload rate at Pi</span>
        <span><i class="traffic-key download"></i>Download rate at Pi</span>
        <span><i class="traffic-key isp-upload"></i>ISP upload plan</span>
        <span><i class="traffic-key isp-download"></i>ISP download plan</span>
        <span id="traffic-profile-upload-legend"><i class="traffic-key profile-upload"></i>Default queue upload limit</span>
        <span id="traffic-profile-download-legend"><i class="traffic-key profile-download"></i>Default queue download limit</span>
      </div>
    </div>
    <p id="traffic-profile-status" class="traffic-profile-status" role="status" aria-live="polite">Select one device to show its applied default queue limits.</p>
    <p class="traffic-note">Traffic lines count bytes at JitterSim before its queues. They do not show the speed a device receives. Dashed lines show configured limits. The ISP plan line is shared with household traffic and is not a live measurement of spare capacity. Service-specific queues may use different limits. Only assigned devices are included. Check the admin token to start; sampling pauses when this tab is hidden.</p>
  </section>
  <section class="card uplink-card">
    <div class="section-heading">
      <div>
        <h2>Upstream router connection</h2>
        <p>What the Pi can see through Ethernet</p>
      </div>
      <span id="uplink-state" class="count-badge">Check token</span>
    </div>
    <div id="uplink-details" class="uplink-grid" aria-live="polite">Enter the admin token and choose Check token to view the router connection.</div>
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
      <label>
        <span class="field-label">Admin token (kept in this page only)<span class="help-icon token-help" id="admin-token-help" tabindex="0" role="img" aria-label="Admin token retrieval commands" data-help="The command to print the token is being prepared.">?</span></span>
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
      <p class="form-note">Select saved devices or connected clients. Choose a profile override when adding an unassigned client. A device can belong to one group.</p>
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
        <h2>Browser service profile</h2>
        <p>Apply a profile to named HTTPS sites on one managed test browser</p>
      </div>
      <span id="service-assignment-count" class="count-badge">No service profiles</span>
    </div>
    <form id="service-assignment-form">
      <div class="fields">
        <label>Service ID
          <input id="service-assignment-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}" placeholder="alice-video">
        </label>
        <label>Service name
          <input id="service-assignment-name" required placeholder="Video sites">
        </label>
        <label>Test device
          <select id="service-assignment-device" required></select>
        </label>
        <label>Profile for these sites
          <select id="service-assignment-profile" required></select>
        </label>
        <label class="quota-check"><input id="service-assignment-enabled" type="checkbox" checked> Enabled</label>
      </div>
      <label>HTTPS hostnames, one per line
        <textarea id="service-assignment-domains" rows="4" required placeholder="netflix.com&#10;google.com&#10;youtube.com"></textarea>
      </label>
      <p class="form-note">Includes subdomains of each name. Configure the Mac browser's HTTPS proxy as 192.168.50.1:3128 while connected to the test Wi-Fi. Other proxied sites keep the device profile. Apps and connections that bypass the proxy are outside this control.</p>
      <button class="primary" type="submit">Save browser service profile</button>
    </form>
    <div id="service-assignment-list" class="quota-list" aria-live="polite">Enter the admin token to view service profiles.</div>
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
      <p class="form-note">Only domains observed from the selected device appear here. Select all names used by the service. Matching direct traffic is counted together then multiplied by the quota weight. Browser proxy tunnels are outside this limit.</p>
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
      <pre id="profiles">Enter the admin token and choose Check token to load.</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Devices</h2><p>Assigned test clients</p></div>
      <pre id="devices">Enter the admin token and choose Check token to load.</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Compiled plan</h2><p>Current traffic rules</p></div>
      <pre id="plan">Enter the admin token and choose Check token to load.</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Runtime state</h2><p>Last helper result</p></div>
      <pre id="state">Enter the admin token and choose Check token to load.</pre>
    </section>
    <section class="card">
      <div class="section-heading"><h2>Access rules</h2><p>Saved MAC and website policy</p></div>
      <pre id="policies">Enter the admin token and choose Check token to load.</pre>
    </section>
    <section class="card analytics-card">
      <div class="section-heading">
        <div><h2>Random website choices</h2><p>Scheduled profile rerolls</p></div>
        <button id="random-website-refresh" type="button">Refresh</button>
      </div>
      <p class="analytics-note">Counts whether each configured website stayed in or was removed from a device's profile list. Only saved choices appear here. These are not site visits or confirmed network blocks.</p>
      <div class="analytics-controls">
        <label for="random-website-device">Device
          <select id="random-website-device"><option value="">All devices</option></select>
        </label>
        <label for="random-website-days">Date range
          <select id="random-website-days">
            <option value="7">Last 7 days</option>
            <option value="30" selected>Last 30 days</option>
          </select>
        </label>
      </div>
      <p id="random-website-status" class="analytics-status" role="status" aria-live="polite">Enter the admin token to view randomized website choices.</p>
      <div class="analytics-table-wrap">
        <table class="analytics-table">
          <thead><tr><th scope="col">Device</th><th scope="col">Website</th><th scope="col">Kept</th><th scope="col">Removed</th><th scope="col">Active days</th></tr></thead>
          <tbody id="random-website-rows"></tbody>
        </table>
      </div>
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
          const applyHint = data.error === "network helper apply failed" &&
            typeof data.hint === "string"
            ? "Network helper apply failed. " + data.hint
            : data.error || response.statusText;
          const error = new Error(applyHint);
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

    const adminTokenHelp = element("admin-token-help");
    adminTokenHelp.dataset.help =
      "On your Mac, run this command:\\n" +
      "ssh -t -i ~/.ssh/jittersim_ed25519 jittersim@" + window.location.hostname +
      " \\\"sudo sed -n 's/^JITTERSIM_ADMIN_TOKEN=//p' /etc/jittersim/control.env\\\"\\n" +
      "Copy the output into this field. Keep the token private.";

    const privateDataPanelIds = [
      "profiles", "devices", "plan", "state", "policies",
      "connected-devices", "unaffected-devices", "uplink-details"
    ];

    function setPrivateDataPanels(message, state = "Check token") {
      privateDataPanelIds.forEach((panelId) => {
        element(panelId).textContent = message;
      });
      element("connected-count").textContent = state;
      element("uplink-state").textContent = state;
      element("runtime-summary").textContent = message;
      element("runtime-summary").className = "runtime-summary unknown";
    }

    async function loadPrivateDataPanel(panelId, load) {
      try {
        await load();
      } catch (error) {
        element(panelId).textContent = "Could not load: " + error.message;
      }
    }

    let helperConfigured = false;
    let adminTokenVerified = false;
    let profiles = [];
    let groups = [];
    let savedDevices = [];
    let connectedClients = [];
    let lastConnectedData = null;
    const connectedDrafts = new Map();
    const clearConnectedDrafts = new Set();
    let observedDomainsByDevice = {};
    let randomWebsiteAnalyticsRequestVersion = 0;
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
    let currentIspTrafficLimits = { upload_kbit: null, download_kbit: 100000 };
    let latestTrafficReferences = {
      isp_upload: null,
      isp_download: 100,
      profile_upload: null,
      profile_download: null
    };

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

    function trafficRateMbit(kbit) {
      return kbit === null || kbit === undefined ? null : kbit / 1000;
    }

    function trafficRateFromInput(id) {
      const raw = element(id).value.trim();
      if (raw === "") return null;
      const rate = Number(raw);
      const tenths = Math.round(rate * 10);
      if (!Number.isFinite(rate) || rate < 0.1 || rate > 1000 || Math.abs(rate * 10 - tenths) > 1e-7) {
        throw new Error("Enter a rate from 0.1 to 1000 Mbit/s in 0.1 Mbit/s steps, or leave it empty.");
      }
      return tenths * 100;
    }

    function setTrafficRateInputs(settings) {
      currentIspTrafficLimits = {
        upload_kbit: settings.upload_kbit,
        download_kbit: settings.download_kbit
      };
      latestTrafficReferences.isp_upload = trafficRateMbit(settings.upload_kbit);
      latestTrafficReferences.isp_download = trafficRateMbit(settings.download_kbit);
      element("traffic-isp-upload").value = settings.upload_kbit === null
        ? ""
        : (settings.upload_kbit / 1000).toFixed(1);
      element("traffic-isp-download").value = settings.download_kbit === null
        ? ""
        : (settings.download_kbit / 1000).toFixed(1);
      drawTrafficChart();
    }

    async function loadTrafficSettings() {
      const settings = await request("/api/traffic-settings", "GET");
      setTrafficRateInputs(settings);
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
      const selectedDevice = element("traffic-device").value;
      const profileSelected = selectedDevice !== "all";
      element("traffic-profile-upload-legend").hidden = !profileSelected;
      element("traffic-profile-download-legend").hidden = !profileSelected;
      const referenceFields = ["isp_upload", "isp_download", "profile_upload", "profile_download"];
      const graphValues = visibleSamples.flatMap((sample) => [
        sample.upload,
        sample.download,
        ...referenceFields.map((field) => sample[field])
      ]).filter((value) => Number.isFinite(value));
      graphValues.push(...Object.values(latestTrafficReferences).filter((value) => Number.isFinite(value)));
      const maxRate = Math.max(0.1, ...graphValues);
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

      function drawSeries(series) {
        context.strokeStyle = series.color;
        context.lineWidth = series.step ? 1.8 : 2.5;
        context.lineJoin = "round";
        context.lineCap = "round";
        context.setLineDash(series.dash || []);
        context.beginPath();
        let started = false;
        let previousY = 0;
        visibleSamples.forEach((sample) => {
          const rate = sample[series.field];
          if (!Number.isFinite(rate)) {
            if (series.step) started = false;
            return;
          }
          const age = now - sample.time;
          const x = plot.right - plotWidth * age / TRAFFIC_WINDOW_MS;
          const y = plot.bottom - plotHeight * rate / scaleMax;
          if (!started) {
            context.moveTo(x, y);
            started = true;
          } else if (series.step) {
            context.lineTo(x, previousY);
            context.lineTo(x, y);
          } else {
            context.lineTo(x, y);
          }
          previousY = y;
        });
        if (started) context.stroke();
        context.setLineDash([]);
      }

      [
        { field: "isp_upload", color: "#75a9ff", dash: [8, 6], step: true },
        { field: "isp_download", color: "#75d7c2", dash: [8, 6], step: true },
        { field: "profile_upload", color: "#ffa76c", dash: [2, 5], step: true },
        { field: "profile_download", color: "#edc36a", dash: [2, 5], step: true },
        { field: "upload", color: "#70b7ff" },
        { field: "download", color: "#70d7a9" }
      ].forEach(drawSeries);

      if (!visibleSamples.length) {
        referenceFields.forEach((field) => {
          const value = latestTrafficReferences[field];
          if (!Number.isFinite(value)) return;
          const colors = {
            isp_upload: "#75a9ff",
            isp_download: "#75d7c2",
            profile_upload: "#ffa76c",
            profile_download: "#edc36a"
          };
          context.strokeStyle = colors[field];
          context.lineWidth = 1.8;
          context.setLineDash(field.startsWith("isp_") ? [8, 6] : [2, 5]);
          const y = plot.bottom - plotHeight * value / scaleMax;
          context.beginPath();
          context.moveTo(plot.left, y);
          context.lineTo(plot.right, y);
          context.stroke();
        });
        context.setLineDash([]);
      }
      if (!visibleSamples.length) {
        context.fillStyle = "#91a8c1";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText("Waiting for traffic samples...", plot.left + plotWidth / 2, plot.top + plotHeight / 2);
      }
    }

    function trafficReferencesFromResponse(data) {
      const ispLimits = data.isp_limits || currentIspTrafficLimits;
      if (data.isp_limits) {
        currentIspTrafficLimits = {
          upload_kbit: data.isp_limits.upload_kbit,
          download_kbit: data.isp_limits.download_kbit
        };
      }
      const selectedDevice = element("traffic-device").value;
      const deviceLimits = selectedDevice === "all" || !data.profile_limits_available
        ? null
        : (data.profile_limits || {})[selectedDevice];
      const profileAvailable = selectedDevice !== "all" &&
        data.profile_limits_available === true && deviceLimits !== undefined;
      element("traffic-profile-status").textContent = selectedDevice === "all"
        ? "Select one device to see its applied default queue limit. Service-specific limits are not shown."
        : !data.profile_limits_available
          ? "No applied plan is available. Apply the configuration to see current default queue limits."
          : !profileAvailable
            ? "The selected device has no applied default queue limits available."
            : deviceLimits.upload_kbit === null && deviceLimits.download_kbit === null
              ? "The selected device has no default queue bandwidth cap. Service-specific queues may use other limits."
              : "The profile line is the applied default queue limit. Service-specific queues may use other limits.";
      return {
        isp_upload: trafficRateMbit(ispLimits.upload_kbit),
        isp_download: trafficRateMbit(ispLimits.download_kbit),
        profile_upload: profileAvailable ? trafficRateMbit(deviceLimits.upload_kbit) : null,
        profile_download: profileAvailable ? trafficRateMbit(deviceLimits.download_kbit) : null
      };
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
        latestTrafficReferences = trafficReferencesFromResponse(data);
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
            trafficSamples.push({
              time: sampledAt,
              upload: upload,
              download: download,
              ...latestTrafficReferences
            });
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
      refresh().then(() => {
        if (adminTokenVerified && helperConfigured && !trafficSamplingAllowed) {
          return startTrafficSampling();
        }
      }).catch((error) => {
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
      summary.textContent = labels[state.observed_state] || "Runtime state unavailable";
      summary.className = "runtime-summary " + state.observed_state;
    }

    function renderConnectedDevices(data) {
      const container = element("connected-devices");
      const count = element("connected-count");
      const expandedLabels = new Set(
        Array.from(container.querySelectorAll(".connected-label-editor[open]"))
          .map((editor) => editor.dataset.macAddress)
      );
      container.querySelectorAll(".connected-row[data-mac-address]").forEach((row) => {
        if (clearConnectedDrafts.has(row.dataset.macAddress)) {
          connectedDrafts.delete(row.dataset.macAddress);
          return;
        }
        const draft = connectedDrafts.get(row.dataset.macAddress) || {};
        row.querySelectorAll("[data-draft][data-dirty]").forEach((field) => { draft[field.dataset.draft] = field.value; });
        connectedDrafts.set(row.dataset.macAddress, draft);
      });
      clearConnectedDrafts.clear();
      container.replaceChildren();
      const devices = data.devices || [];
      connectedClients = devices;
      lastConnectedData = data;
      const currentMacs = new Set(devices.map((device) => device.mac_address));
      for (const mac of connectedDrafts.keys()) if (!currentMacs.has(mac)) connectedDrafts.delete(mac);
      updateGroupMemberOptions();
      const deviceTypeNames = {
        unknown: "Unknown",
        phone: "Phone",
        tablet: "Tablet",
        laptop: "Laptop",
        desktop: "Desktop",
        tv_console: "TV or console",
        other: "Other"
      };
      if (!data.available) {
        count.textContent = "Unavailable";
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.message || "Connected-client information is unavailable.";
        container.appendChild(empty);
      } else {
        const connectionLabel = data.association_status_available ? " connected" : " visible";
        count.textContent = devices.length + connectionLabel;
        if (!data.association_status_available) {
          const warning = document.createElement("p");
          warning.className = "device-empty";
          warning.textContent =
            "Wi-Fi connection status is unavailable. Listed IP neighbors may be disconnected " +
            "and profile actions are disabled.";
          container.appendChild(warning);
        }
      }
      if (data.available && devices.length === 0) {
        const empty = document.createElement("p");
        empty.className = "device-empty";
        empty.textContent = data.association_status_available
          ? "No devices are connected to the test Wi-Fi."
          : "No recent IP neighbors are visible.";
        container.appendChild(empty);
      }
      devices.forEach((device) => {
        const row = document.createElement("div");
        row.className = "connected-row";
        row.dataset.macAddress = device.mac_address;
        const draft = connectedDrafts.get(device.mac_address) || {};
        const dot = document.createElement("span");
        dot.className = "connected-dot";
        const details = document.createElement("div");
        const address = document.createElement("p");
        address.className = "connected-address";
        address.textContent = device.display_name || "Unnamed client";
        const metadata = document.createElement("p");
        metadata.className = "connected-meta";
        const metadataParts = [device.ipv4_address || "IP address pending", device.mac_address];
        if (device.signal_dbm !== undefined && device.signal_dbm !== null) {
          metadataParts.push("Signal " + device.signal_dbm + " dBm");
        }
        metadata.textContent = metadataParts.join(" · ");
        const deviceType = document.createElement("p");
        deviceType.className = "connected-meta";
        const typeName = deviceTypeNames[device.device_type] || "Unknown";
        deviceType.textContent = "Type: " + typeName + (
          device.device_type_source === "hostname suggestion" ? " (suggested)" : ""
        );
        const assignmentInfo = document.createElement("p");
        assignmentInfo.className = "connected-meta";
        assignmentInfo.textContent = device.unaffected ? "Unaffected by all profiles and quotas" :
          "Base profile: " + (device.assigned_profile_id || "Unassigned") +
          (device.group_name ? " · Group: " + device.group_name + (device.group_profile_id ? " (override: " + device.group_profile_id + ")" : "") : "");
        details.append(address, metadata, deviceType, assignmentInfo);
        if (device.hostname) {
          const hostname = document.createElement("p");
          hostname.className = "connected-meta";
          hostname.textContent = "Network name: " + device.hostname;
          details.appendChild(hostname);
        }
        const labelEditor = document.createElement("details");
        labelEditor.className = "connected-label-editor";
        labelEditor.dataset.macAddress = device.mac_address;
        labelEditor.open = expandedLabels.has(device.mac_address);
        const labelSummary = document.createElement("summary");
        labelSummary.textContent = "Edit name and type";
        const labelForm = document.createElement("div");
        labelForm.className = "connected-label-form";
        const nameLabel = document.createElement("label");
        nameLabel.textContent = "Display name";
        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.maxLength = 64;
        nameInput.dataset.draft = "name";
        nameInput.value = draft.name ?? (device.saved_display_name || device.hostname || "");
        nameInput.placeholder = "Optional name";
        nameLabel.appendChild(nameInput);
        const typeLabel = document.createElement("label");
        typeLabel.textContent = "Device type";
        const typeSelect = document.createElement("select");
        typeSelect.setAttribute(
          "aria-label",
          "Device type for " + (device.display_name || device.mac_address)
        );
        Object.entries(deviceTypeNames).forEach(([value, name]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = name;
          option.selected = value === device.device_type;
          typeSelect.appendChild(option);
        });
        typeSelect.dataset.draft = "type";
        if (draft.type !== undefined) typeSelect.value = draft.type;
        typeLabel.appendChild(typeSelect);
        const saveLabelButton = document.createElement("button");
        saveLabelButton.type = "button";
        saveLabelButton.textContent = "Save details";
        saveLabelButton.addEventListener("click", () => {
          saveConnectedDeviceLabel(device, nameInput, typeSelect, saveLabelButton);
        });
        labelForm.append(nameLabel, typeLabel, saveLabelButton);
        if (device.has_saved_label) {
          const clearLabelButton = document.createElement("button");
          clearLabelButton.type = "button";
          clearLabelButton.textContent = "Clear saved details";
          clearLabelButton.addEventListener("click", () => {
            clearConnectedDeviceLabel(device, clearLabelButton);
          });
          labelForm.appendChild(clearLabelButton);
        }
        labelEditor.append(labelSummary, labelForm);
        details.appendChild(labelEditor);
        const state = document.createElement("span");
        state.className = "connected-state";
        state.textContent = device.association_verified ? "Connected" : "Status unknown";
        const controls = document.createElement("div");
        controls.className = "connected-controls";
        const profileSelect = document.createElement("select");
        profileSelect.setAttribute(
          "aria-label",
          "Profile for " + (device.display_name || device.mac_address)
        );
        profileSelect.dataset.draft = "profile";
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
        if (draft.profile !== undefined) profileSelect.value = draft.profile;
        profileSelect.disabled = !!device.unaffected || !device.ipv4_address || !device.association_verified;
        const applyButton = document.createElement("button");
        applyButton.type = "button";
        applyButton.textContent = device.assigned_profile_id ? "Apply profile" : "Assign profile";
        applyButton.disabled = !!device.unaffected || !profileSelect.value || !helperConfigured || profiles.length === 0 ||
          !device.ipv4_address || !device.association_verified;
        profileSelect.addEventListener("change", () => {
          applyButton.disabled = !!device.unaffected || !helperConfigured || profileSelect.value === "" ||
            !device.ipv4_address || !device.association_verified;
        });
        applyButton.addEventListener("click", () => {
          applyProfileToDevice(device, profileSelect.value, applyButton);
        });
        const groupSelect = document.createElement("select");
        groupSelect.dataset.draft = "group";
        groupSelect.setAttribute("aria-label", "Group for " + (device.display_name || device.mac_address));
        const groupPlaceholder = document.createElement("option");
        groupPlaceholder.value = "";
        groupPlaceholder.textContent = groups.length ? "Choose group..." : "Create a group below";
        groupSelect.appendChild(groupPlaceholder);
        groups.forEach((group) => {
          const option = document.createElement("option");
          option.value = group.id;
          option.textContent = group.name;
          option.disabled = (!!device.group_id && device.group_id !== group.id) || (!device.assigned_device_id && !group.profile_id);
          option.selected = group.id === device.group_id;
          groupSelect.appendChild(option);
        });
        if (draft.group !== undefined) groupSelect.value = draft.group;
        const groupButton = document.createElement("button");
        groupButton.type = "button";
        groupButton.textContent = "Add to group";
        const syncGroup = () => {
          const selected = groups.find((group) => group.id === groupSelect.value);
          groupButton.disabled = !helperConfigured || !device.association_verified || !device.ipv4_address ||
            !selected || selected.id === device.group_id || (!device.assigned_device_id && !selected.profile_id);
        };
        groupSelect.disabled = !helperConfigured || !device.association_verified || !device.ipv4_address || !groups.length;
        groupSelect.addEventListener("change", syncGroup);
        syncGroup();
        groupButton.addEventListener("click", () => connectedControlAction(
          "/api/connected-devices/group", {group_id: groupSelect.value, ipv4_address: device.ipv4_address, mac_address: device.mac_address},
          groupButton, "Group membership saved and applied."
        ));
        const protectionButton = document.createElement("button");
        protectionButton.type = "button";
        protectionButton.textContent = device.unaffected ? "Remove protection" : "Make unaffected";
        protectionButton.setAttribute("aria-label", protectionButton.textContent + " for " + (device.display_name || device.mac_address));
        protectionButton.addEventListener("click", () => connectedControlAction(
          device.unaffected ? "/api/unaffected-devices/delete" : "/api/unaffected-devices",
          {mac_address: device.mac_address, display_name: device.display_name || ""}, protectionButton,
          device.unaffected ? "Protection removed. Saved profiles can apply again." : "Protection saved and applied. This device is unaffected by profiles."
        ));
        controls.append(profileSelect, applyButton, groupSelect, groupButton, protectionButton);
        [...labelForm.querySelectorAll("[data-draft]"), profileSelect, groupSelect].forEach((field) => {
          if (draft[field.dataset.draft] !== undefined) field.dataset.dirty = "true";
          field.addEventListener("input", () => { field.dataset.dirty = "true"; });
          field.addEventListener("change", () => { field.dataset.dirty = "true"; });
        });
        row.append(dot, details, state, controls);
        container.appendChild(row);
      });
      renderSavedConnectedDeviceLabels(
        container,
        data.saved_labels || [],
        new Set(devices.map((device) => device.mac_address)),
        deviceTypeNames
      );
    }

    function renderSavedConnectedDeviceLabels(container, labels, connectedMacAddresses, typeNames) {
      const disconnectedLabels = labels.filter(
        (label) => !connectedMacAddresses.has(label.mac_address)
      );
      if (disconnectedLabels.length === 0) return;
      const section = document.createElement("div");
      section.className = "saved-device-labels";
      const heading = document.createElement("h3");
      heading.textContent = "Saved labels for devices not listed above";
      const note = document.createElement("p");
      note.className = "device-empty";
      note.textContent = "Remove old labels here if a device's private Wi-Fi address changed.";
      section.append(heading, note);
      disconnectedLabels.forEach((label) => {
        const row = document.createElement("div");
        row.className = "connected-row saved-device-label-row";
        const details = document.createElement("div");
        const name = document.createElement("p");
        name.className = "connected-address";
        name.textContent = label.display_name || "Unnamed client";
        const mac = document.createElement("p");
        mac.className = "connected-meta";
        mac.textContent = label.mac_address;
        details.append(name, mac);
        const type = document.createElement("span");
        type.className = "connected-state";
        type.textContent = typeNames[label.device_type] || "Unknown";
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.textContent = "Remove saved label";
        removeButton.addEventListener("click", () => {
          clearConnectedDeviceLabel(label, removeButton);
        });
        row.append(details, type, removeButton);
        section.appendChild(row);
      });
      container.appendChild(section);
    }

    async function saveConnectedDeviceLabel(device, nameInput, typeSelect, button) {
      setButtonBusy(button, true);
      try {
        await request("/api/connected-device-labels", "POST", {
          mac_address: device.mac_address,
          display_name: nameInput.value,
          device_type: typeSelect.value
        });
        showMessage("Saved device name and type.", "success");
        await loadConnectedDevices();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function clearConnectedDeviceLabel(device, button) {
      setButtonBusy(button, true);
      try {
        await request("/api/connected-device-labels/delete", "POST", {
          mac_address: device.mac_address
        });
        showMessage("Removed saved device details.", "success");
        await loadConnectedDevices();
      } catch (error) {
        showMessage(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
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
        const result = await request("/api/connected-devices/apply-profile", "POST", {
          ipv4_address: device.ipv4_address,
          mac_address: device.mac_address,
          profile_id: profileId
        });
        clearConnectedDrafts.add(device.mac_address);
        refreshAfterSuccess((result.message || "Base profile saved and applied.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        showMessage(error.message, "error");
        await loadConnectedDevices();
      } finally {
        setButtonBusy(button, false);
        setActionBusy(false, "/api/apply");
      }
    }

    async function connectedControlAction(path, payload, button, success) {
      setButtonBusy(button, true);
      showMessage("Saving network settings...", "working");
      try {
        const result = await request(path, "POST", payload);
        clearConnectedDrafts.add(payload.mac_address);
        refreshAfterSuccess((result.live_applied ? success : result.message || "Settings saved. Apply them in hardware mode.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        showMessage(error.message, "error");
        await Promise.all([loadUnaffectedDevices(), loadConnectedDevices()]);
      } finally {
        setButtonBusy(button, false);
      }
    }

    async function loadUnaffectedDevices() {
      const container = element("unaffected-devices");
      try {
        const data = await request("/api/unaffected-devices", "GET");
        container.replaceChildren();
        if (!data.devices.length) {
          container.textContent = "No devices are protected. Choose Make unaffected beside a connected client.";
        }
        data.devices.forEach((device) => {
          const row = document.createElement("div");
          row.className = "connected-row saved-device-label-row";
          const name = document.createElement("div");
          const title = document.createElement("p");
          title.className = "connected-address";
          title.textContent = device.display_name || "Unnamed device";
          const mac = document.createElement("p");
          mac.className = "connected-meta";
          mac.textContent = device.mac_address;
          name.append(title, mac);
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = "Remove protection";
          button.setAttribute("aria-label", "Remove protection for " + (device.display_name || device.mac_address));
          button.addEventListener("click", () => connectedControlAction("/api/unaffected-devices/delete", {mac_address: device.mac_address}, button, "Protection removed. Saved profiles can apply again."));
          row.append(name, button);
          container.appendChild(row);
        });
      } catch (error) {
        container.textContent = "Could not load protection: " + error.message;
      }
    }

    function updateGroupMemberOptions() {
      const select = element("group-devices");
      if (document.activeElement === select) return;
      const selected = new Set(Array.from(select.selectedOptions).map((option) => option.value));
      select.replaceChildren();
      savedDevices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.id;
        option.textContent = device.name + " (" + device.ipv4_address + ")";
        option.selected = selected.has(option.value);
        select.appendChild(option);
      });
      connectedClients.filter((device) => !device.assigned_device_id && device.association_verified && device.ipv4_address).forEach((device) => {
        const option = document.createElement("option");
        option.value = "live:" + device.mac_address;
        option.textContent = (device.display_name || device.mac_address) + " (connected · unassigned)";
        option.selected = selected.has(option.value);
        select.appendChild(option);
      });
    }

    async function loadConnectedDevices() {
      if (connectedDevicesRequestInFlight) return;
      const container = element("connected-devices");
      const activeElement = document.activeElement;
      if (
        activeElement && container.contains(activeElement) &&
        activeElement.matches("input, select")
      ) return;
      connectedDevicesRequestInFlight = true;
      try {
        const [connectedResult, labelsResult] = await Promise.allSettled([
          request("/api/connected-devices", "GET"),
          request("/api/connected-device-labels", "GET")
        ]);
        const savedLabels = labelsResult.status === "fulfilled"
          ? labelsResult.value.labels || []
          : [];
        if (connectedResult.status === "rejected") {
          renderConnectedDevices({
            available: false,
            devices: [],
            saved_labels: savedLabels,
            message: connectedResult.reason.message
          });
        } else {
          renderConnectedDevices({ ...connectedResult.value, saved_labels: savedLabels });
        }
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
        renderObservations({ available: false, devices: [], message: error.message });
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
            const match = /^(\\d{1,2}):(\\d{2})$/.exec(value);
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

    function renderServiceAssignments(data) {
      const assignments = data.service_assignments || [];
      const container = element("service-assignment-list");
      element("service-assignment-count").textContent = assignments.length +
        (assignments.length === 1 ? " service profile" : " service profiles");
      container.replaceChildren();
      if (!assignments.length) {
        container.textContent = data.error || "No browser service profiles saved.";
        return;
      }
      assignments.forEach((assignment) => {
        const row = document.createElement("div");
        row.className = "quota-row";
        const heading = document.createElement("div");
        heading.className = "quota-heading";
        const title = document.createElement("strong");
        title.textContent = assignment.name + " (" + assignment.id + ")";
        const detail = document.createElement("span");
        detail.textContent = assignment.device_name + " → " + assignment.profile_name +
          (assignment.enabled ? "" : " · disabled");
        heading.append(title, detail);
        const domains = document.createElement("span");
        domains.textContent = assignment.domains.join(", ");
        const actions = document.createElement("div");
        actions.className = "quota-actions";
        const editButton = document.createElement("button");
        editButton.type = "button";
        editButton.textContent = "Edit";
        editButton.addEventListener("click", () => editServiceAssignment(assignment));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", async () => {
          setButtonBusy(deleteButton, true);
          try {
            await request("/api/service-assignments/delete", "POST", { id: assignment.id });
            refreshAfterSuccess("Browser service profile deleted.");
          } catch (error) {
            showMessage(error.message, "error");
          } finally {
            setButtonBusy(deleteButton, false);
          }
        });
        actions.append(editButton, deleteButton);
        row.append(heading, domains, actions);
        container.appendChild(row);
      });
    }

    function editServiceAssignment(assignment) {
      element("service-assignment-id").value = assignment.id;
      element("service-assignment-name").value = assignment.name;
      element("service-assignment-device").value = assignment.device_id;
      element("service-assignment-profile").value = assignment.profile_id;
      element("service-assignment-domains").value = assignment.domains.join("\\n");
      element("service-assignment-enabled").checked = assignment.enabled;
      element("service-assignment-form").scrollIntoView({ behavior: "smooth", block: "center" });
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

    async function loadServiceAssignments() {
      try {
        renderServiceAssignments(await request("/api/service-assignments", "GET"));
      } catch (error) {
        renderServiceAssignments({ service_assignments: [], error: error.message });
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
        "service-assignment-profile",
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
      savedDevices = data.devices;
      updateGroupMemberOptions();
      [
        "schedule-device",
        "timer-device",
        "progression-device",
        "data-limit-device",
        "service-quota-device",
        "service-assignment-device",
        "time-quota-device"
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
        if (lastConnectedData && !element("connected-devices").contains(document.activeElement)) renderConnectedDevices(lastConnectedData);
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

    function formatUtcDay(timestamp) {
      return new Date(timestamp * 1000).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC"
      });
    }

    function renderRandomWebsiteAnalytics(data) {
      const rows = element("random-website-rows");
      rows.replaceChildren();
      (data.rows || []).forEach((item) => {
        const row = document.createElement("tr");
        const device = document.createElement("td");
        device.textContent = item.device_name + " (" + item.device_id + ")";
        const domain = document.createElement("td");
        domain.className = "analytics-domain";
        domain.textContent = item.domain;
        const kept = document.createElement("td");
        kept.textContent = Number(item.kept_count).toLocaleString();
        const removed = document.createElement("td");
        removed.textContent = Number(item.removed_count).toLocaleString();
        const activeDays = document.createElement("td");
        activeDays.textContent = Number(item.days_with_events).toLocaleString();
        row.append(device, domain, kept, removed, activeDays);
        rows.appendChild(row);
      });
      const status = element("random-website-status");
      const oldestDay = data.oldest_available_day_utc;
      const history = oldestDay === null
        ? "No history has been saved yet."
        : "Oldest saved result: " + formatUtcDay(oldestDay) + " UTC. Older history may have expired or been trimmed.";
      status.className = "analytics-status";
      if (data.truncated) {
        status.textContent = "Showing the 5,000 highest-count device and website totals. Choose one device or a shorter range. " + history;
      } else if (!data.rows.length) {
        status.textContent = "No scheduled randomized website choices were recorded in this range. " + history;
      } else {
        status.textContent = data.rows.length + " device and website totals. " + history;
      }
    }

    async function loadRandomWebsiteAnalytics() {
      const status = element("random-website-status");
      const rows = element("random-website-rows");
      if (!element("admin-token").value.trim()) {
        randomWebsiteAnalyticsRequestVersion += 1;
        rows.replaceChildren();
        status.className = "analytics-status";
        status.textContent = "Enter the admin token to view randomized website choices.";
        return;
      }
      const requestVersion = ++randomWebsiteAnalyticsRequestVersion;
      status.className = "analytics-status";
      status.textContent = "Loading randomized website choices...";
      try {
        const deviceData = await request("/api/devices", "GET");
        const deviceSelect = element("random-website-device");
        const selectedDevice = deviceSelect.value;
        deviceSelect.replaceChildren();
        const allDevices = document.createElement("option");
        allDevices.value = "";
        allDevices.textContent = "All devices";
        deviceSelect.appendChild(allDevices);
        (deviceData.devices || []).forEach((device) => {
          const option = document.createElement("option");
          option.value = device.id;
          option.textContent = device.name + " (" + device.id + ")";
          option.selected = device.id === selectedDevice;
          deviceSelect.appendChild(option);
        });
        const parameters = new URLSearchParams({
          days: element("random-website-days").value,
          device_id: deviceSelect.value
        });
        const data = await request("/api/random-website-analytics?" + parameters.toString(), "GET");
        if (requestVersion === randomWebsiteAnalyticsRequestVersion) {
          renderRandomWebsiteAnalytics(data);
        }
      } catch (error) {
        if (requestVersion !== randomWebsiteAnalyticsRequestVersion) return;
        rows.replaceChildren();
        status.className = "analytics-status error";
        status.textContent = "Could not load randomized website history: " + error.message;
      }
    }

    async function loadAdminTools() {
      if (!element("admin-token").value.trim()) {
        element("diagnostics").textContent = "Enter the admin token to inspect diagnostics.";
        element("action-history").textContent = "Enter the admin token to view recent actions.";
        randomWebsiteAnalyticsRequestVersion += 1;
        element("random-website-rows").replaceChildren();
        element("random-website-status").textContent = "Enter the admin token to view randomized website choices.";
        element("random-website-status").className = "analytics-status";
        return { ok: false, message: "Enter the admin token first." };
      }
      try {
        const data = await request("/api/diagnostics", "GET");
        element("diagnostics").textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        element("diagnostics").textContent = error.message;
        element("action-history").textContent = error.message;
        randomWebsiteAnalyticsRequestVersion += 1;
        element("random-website-rows").replaceChildren();
        element("random-website-status").textContent = error.message;
        element("random-website-status").className = "analytics-status error";
        return { ok: false, message: error.message };
      }
      let actionHistoryLoaded = true;
      try {
        const data = await request("/api/action-history", "GET");
        element("action-history").textContent = data.actions.length
          ? data.actions.map((action) => new Date(action.happened_at * 1000).toLocaleString() +
              " — " + action.action + " — " + action.status_code).join("\\n")
          : "No admin actions have been recorded.";
      } catch (error) {
        element("action-history").textContent = error.message;
        actionHistoryLoaded = false;
      }
      await loadRandomWebsiteAnalytics();
      return {
        ok: true,
        message: actionHistoryLoaded
          ? "Token accepted. Diagnostics loaded."
          : "Token accepted. Diagnostics loaded, but action history could not be loaded."
      };
    }

    async function checkAdminToken() {
      const button = element("check-admin-token");
      const status = element("token-check-status");
      if (!element("admin-token").value.trim()) {
        adminTokenVerified = false;
        setPrivateDataPanels("Enter the admin token and choose Check token to load.");
        status.textContent = "Enter the admin token first.";
        status.className = "token-check-status error";
        showMessage("Enter the admin token first.", "error");
        element("admin-token").focus();
        return;
      }
      adminTokenVerified = false;
      setPrivateDataPanels("Checking the admin token...", "Checking...");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Checking...";
      status.textContent = "Checking the token and loading diagnostics...";
      showMessage("Checking the admin token...", "working");
      try {
        const result = await loadAdminTools();
        if (result.ok) {
          adminTokenVerified = true;
          await refresh();
          await loadTrafficSettings();
          await startTrafficSampling();
        } else {
          adminTokenVerified = false;
          setPrivateDataPanels("Token check failed: " + result.message);
        }
        status.textContent = result.ok ? result.message : "Token check failed: " + result.message;
        status.className = "token-check-status " + (result.ok ? "success" : "error");
        showMessage(status.textContent, result.ok ? "success" : "error");
      } catch (error) {
        adminTokenVerified = false;
        setPrivateDataPanels("Token check failed: " + error.message);
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
      await loadPrivateDataPanel("profiles", loadProfiles);
      await loadHealth();
      await loadObservations();
      await Promise.all([
        loadPrivateDataPanel("devices", () => show("/api/devices", "devices", "devices")),
        loadPrivateDataPanel("plan", () => show("/api/plan", "plan", "plan")),
        loadPrivateDataPanel("state", loadState),
        loadDevicesForSchedule(),
        loadGroups(),
        loadSchedules(),
        loadTimers(),
        loadProgressions(),
        loadPrivateDataPanel("policies", loadPolicies),
        loadConnectedDevices(),
        loadUnaffectedDevices(),
        loadUplink(),
        loadQuotas(),
        loadServiceAssignments(),
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
        refreshAfterSuccess(successMessage + (data.warning ? " " + data.warning : ""));
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

    element("unaffected-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await connectedControlAction("/api/unaffected-devices", {
        mac_address: element("unaffected-mac").value,
        display_name: element("unaffected-name").value
      }, event.currentTarget.querySelector("button"), "Protection saved and applied.");
    });

    element("group-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const selectedIds = Array.from(element("group-devices").selectedOptions).map((option) => option.value);
        const deviceIds = selectedIds.filter((id) => !id.startsWith("live:"));
        const connected = selectedIds.filter((id) => id.startsWith("live:")).map((id) => {
          const client = connectedClients.find((device) => "live:" + device.mac_address === id);
          if (!client) throw new Error("A selected client disconnected. Refresh the list.");
          return {ipv4_address: client.ipv4_address, mac_address: client.mac_address};
        });
        if (!selectedIds.length) throw new Error("Select at least one device for the group.");
        const result = await request("/api/groups", "POST", {
          id: element("group-id").value,
          name: element("group-name").value,
          profile_id: element("group-profile").value || null,
          device_ids: deviceIds,
          connected_clients: connected
        });
        savedDevices.filter((device) => deviceIds.includes(device.id)).forEach((device) => clearConnectedDrafts.add(device.mac_address));
        connected.forEach((device) => clearConnectedDrafts.add(device.mac_address));
        refreshAfterSuccess((result.live_applied ? "Device group saved and applied." : result.message || "Device group saved.") + (result.warning ? " " + result.warning : ""));
      } catch (error) {
        showMessage(error.message, "error");
        await Promise.all([loadGroups(), loadConnectedDevices()]);
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
            : "Timer saved. Apply the configuration in hardware mode to start it.",
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
            : "Progression saved. Apply the configuration in hardware mode to start it.",
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
    element("traffic-rate-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector("button[type='submit']");
      const status = element("traffic-rate-status");
      button.disabled = true;
      try {
        const settings = await request("/api/traffic-settings", "POST", {
          upload_kbit: trafficRateFromInput("traffic-isp-upload"),
          download_kbit: trafficRateFromInput("traffic-isp-download")
        });
        setTrafficRateInputs(settings);
        resetTrafficHistory();
        status.textContent = "ISP speed references saved. They do not change traffic shaping.";
        showMessage("ISP speed references saved.", "success");
        if (trafficSamplingAllowed) await pollTraffic();
      } catch (error) {
        status.textContent = error.message;
        showMessage(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
    element("admin-token").addEventListener("input", () => {
      adminTokenVerified = false;
      setPrivateDataPanels("Token changed. Choose Check token to load.");
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
        if (result.ok) {
          await loadTrafficSettings();
          await startTrafficSampling();
        }
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

    element("service-assignment-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const domains = element("service-assignment-domains").value.split(/[\\s,]+/).filter(Boolean);
        if (!domains.length) throw new Error("Enter at least one HTTPS hostname.");
        const result = await request("/api/service-assignments", "POST", {
          id: element("service-assignment-id").value,
          name: element("service-assignment-name").value,
          device_id: element("service-assignment-device").value,
          profile_id: element("service-assignment-profile").value,
          domains: domains,
          enabled: element("service-assignment-enabled").checked
        });
        refreshAfterSuccess(
          result.live_applied ? "Browser service profile saved and applied." : "Browser service profile saved."
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

    element("random-website-device").addEventListener("change", loadRandomWebsiteAnalytics);
    element("random-website-days").addEventListener("change", loadRandomWebsiteAnalytics);
    element("random-website-refresh").addEventListener("click", loadRandomWebsiteAnalytics);

    window.setInterval(() => { if (adminTokenVerified) loadConnectedDevices(); }, 5000);
    window.setInterval(() => { if (adminTokenVerified) loadTimers(); }, 5000);
    window.setInterval(() => { if (adminTokenVerified) loadProgressions(); }, 5000);
    window.setInterval(() => { if (adminTokenVerified) loadQuotas(); }, 5000);
    window.setInterval(() => { if (adminTokenVerified) loadObservations(); }, 15000);

    loadHealth().catch((error) => {
      showMessage(error.message, "error");
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


def _connected_device_payload(
    device: Dict[str, Any],
    *,
    assignments_by_address: Dict[str, Device],
    labels_by_mac: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """Add saved display details and any matching profile to one station."""

    mac_address = device["mac_address"]
    saved_label = labels_by_mac.get(mac_address)
    hostname = device.get("hostname")
    suggested_type = guess_device_type(hostname)
    assignment = next(
        (saved for saved in assignments_by_address.values() if saved.mac_address == mac_address),
        None,
    )
    return {
        **device,
        "display_name": (
            saved_label["display_name"]
            if saved_label and saved_label["display_name"]
            else hostname or "Unnamed client"
        ),
        "saved_display_name": saved_label["display_name"] if saved_label else "",
        "saved_device_type": saved_label["device_type"] if saved_label else "",
        "has_saved_label": saved_label is not None,
        "device_type": saved_label["device_type"] if saved_label else suggested_type,
        "device_type_source": (
            "saved"
            if saved_label is not None
            else "hostname suggestion"
            if suggested_type != "unknown"
            else "unknown"
        ),
        "assigned_device_id": assignment.id if assignment is not None else None,
        "assigned_device_name": assignment.name if assignment is not None else None,
        "assigned_profile_id": assignment.profile_id if assignment is not None else None,
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


def _service_assignment_payload(
    assignment: ServiceAssignment, *, repository: ConfigRepository
) -> Dict[str, Any]:
    device = repository.get_device(assignment.device_id)
    profile = repository.get_profile(assignment.profile_id)
    return {
        **asdict(assignment),
        "domains": list(assignment.domains),
        "device_name": device.name if device is not None else assignment.device_id,
        "profile_name": profile.name if profile is not None else assignment.profile_id,
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

    def response_for(self, path: str, query: str = "") -> Tuple[int, str, bytes]:
        """Build the response body for a supported GET path."""

        if path == "/":
            return HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8")
        if path == "/api/health":
            helper_reachable = False
            if self.helper_client is not None:
                try:
                    helper_reachable = self.helper_client.ping().get("ok") is True
                except HelperClientError:
                    LOGGER.warning("network helper health check failed")
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "status": "ok" if self.helper_client is None or helper_reachable else "degraded",
                    "mode": "hardware" if self.helper_client is not None else "configuration-only",
                    "helper_configured": self.helper_client is not None,
                    "helper_reachable": helper_reachable,
                }
            )
        if path == "/api/profiles":
            profiles = [_profile_payload(profile) for profile in self.repository.list_profiles()]
            return HTTPStatus.OK, "application/json", _json_bytes({"profiles": profiles})
        if path == "/api/devices":
            devices = [_device_payload(device) for device in self.repository.list_devices()]
            return HTTPStatus.OK, "application/json", _json_bytes({"devices": devices})
        if path == "/api/unaffected-devices":
            return HTTPStatus.OK, "application/json", _json_bytes({
                "devices": [asdict(device) for device in self.repository.list_unaffected_devices()],
                "limit": 256,
            })
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
        if path == "/api/service-assignments":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "service_assignments": [
                        _service_assignment_payload(assignment, repository=self.repository)
                        for assignment in self.repository.list_service_assignments()
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
        if path == "/api/connected-device-labels":
            labels = self.repository.list_connected_device_labels()
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "labels": [
                        {
                            "mac_address": mac_address,
                            "display_name": label["display_name"],
                            "device_type": label["device_type"],
                        }
                        for mac_address, label in labels.items()
                    ]
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
                snapshot = self.helper_client.connected_device_snapshot()
                devices = snapshot["devices"]
            except HelperClientError:
                LOGGER.exception("could not inspect connected clients")
                return (
                    HTTPStatus.BAD_GATEWAY,
                    "application/json",
                    _json_bytes({"error": "could not inspect connected clients"}),
                )
            assignments_by_address = {
                str(device.ipv4_address): device for device in self.repository.list_devices()
            }
            labels_by_mac = self.repository.list_connected_device_labels()
            protected = {item.mac_address for item in self.repository.list_unaffected_devices()}
            groups_by_device = {
                device_id: group
                for group in self.repository.list_device_groups()
                for device_id in group.device_ids
            }
            devices = [
                _connected_device_payload(
                    device,
                    assignments_by_address=assignments_by_address,
                    labels_by_mac=labels_by_mac,
                )
                for device in devices
            ]
            for client in devices:
                group = groups_by_device.get(client["assigned_device_id"])
                client["unaffected"] = client["mac_address"] in protected
                client["group_id"] = group.id if group else None
                client["group_name"] = group.name if group else None
                client["group_profile_id"] = group.profile_id if group else None
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        "available": True,
                        "association_status_available": snapshot["association_status_available"],
                        "devices": devices,
                    }
                ),
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
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
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
                ),
            )
        if path == "/api/plan":
            plan = (
                self.scheduler.build_plan(repository=self.repository)
                if self.scheduler is not None
                else compile_stored_plan(
                    repository=self.repository,
                    connected_snapshot=station_snapshot(self.helper_client)
                    if self.helper_client
                    else None,
                )
            )
            return HTTPStatus.OK, "application/json", _json_bytes({"plan": plan_to_payload(plan)})
        if path == "/api/state":
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes({"state": _runtime_state_payload(self.repository.get_runtime_state())}),
            )
        if path == "/api/action-history":
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes({"actions": list(self.repository.list_recent_actions())}),
            )
        if path == "/api/random-website-analytics":
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=4,
                )
            except ValueError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "too many analytics filters"}
                )
            day_values = parameters.get("days", ["30"])
            device_values = parameters.get("device_id", [""])
            if len(day_values) != 1 or len(device_values) != 1:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "provide one date range and one device filter"}
                )
            try:
                days = int(day_values[0])
            except (TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "days must be between 1 and 30"}
                )
            if not 1 <= days <= 30:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "days must be between 1 and 30"}
                )
            device_id = device_values[0] or None
            if device_id is not None and len(device_id) > 64:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "device id is too long"}
                )
            report = self.repository.list_random_website_analytics(
                days=days,
                device_id=device_id,
            )
            return HTTPStatus.OK, "application/json", _json_bytes(report)
        if path == "/api/backup":
            return HTTPStatus.OK, "application/vnd.sqlite3", self.repository.backup_bytes()
        if path == "/api/traffic-settings":
            return HTTPStatus.OK, "application/json", _json_bytes(
                self.repository.get_traffic_rate_settings()
            )
        if path == "/api/traffic":
            isp_limits = self.repository.get_traffic_rate_settings()
            reset = self.repository.get_runtime_state().observed_state == "reset"
            if self.helper_client is None or reset:
                return HTTPStatus.OK, "application/json", _json_bytes(
                    {
                        "available": False,
                        "message": (
                            "Live traffic needs hardware mode."
                            if self.helper_client is None
                            else "Network is reset. Apply the configuration to start traffic measurements."
                        ),
                        "devices": {},
                        "isp_limits": isp_limits,
                        "profile_limits_available": False,
                        "profile_limits": {},
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
            profile_limits = (
                self.scheduler.applied_queue_bandwidth_limits()
                if self.scheduler is not None
                else None
            )
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "available": True,
                    "sampled_at": time.time(),
                    "devices": device_counters,
                    "isp_limits": isp_limits,
                    "profile_limits_available": profile_limits is not None,
                    "profile_limits": profile_limits or {},
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

        if path == "/api/traffic-settings":
            try:
                if "upload_kbit" not in payload or "download_kbit" not in payload:
                    raise TrafficRateSettingsError("both rate fields are required")
                settings = self.repository.save_traffic_rate_settings(
                    upload_kbit=payload["upload_kbit"],
                    download_kbit=payload["download_kbit"],
                )
            except TrafficRateSettingsError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid ISP rate references"}
                )
            return HTTPStatus.OK, "application/json", _json_bytes(settings)

        if path == "/api/connected-device-labels":
            try:
                mac_address = normalize_device_mac(payload.get("mac_address"))
                display_name = normalize_device_display_name(payload.get("display_name"))
                device_type = normalize_device_type(payload.get("device_type"))
            except DeviceLabelError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "connected-device details are invalid"}
                )
            self.repository.save_connected_device_label(
                mac_address=mac_address,
                display_name=display_name,
                device_type=device_type,
            )
            return HTTPStatus.OK, "application/json", _json_bytes({"saved": True})

        if path == "/api/connected-device-labels/delete":
            try:
                mac_address = normalize_device_mac(payload.get("mac_address"))
            except DeviceLabelError:
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "connected-device address is invalid"}
                )
            self.repository.delete_connected_device_label(mac_address=mac_address)
            return HTTPStatus.OK, "application/json", _json_bytes({"deleted": True})

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

        if path in ("/api/unaffected-devices", "/api/unaffected-devices/delete"):
            return self._change_unaffected_device(payload, remove=path.endswith("/delete"))

        if path in ("/api/groups", "/api/connected-devices/group"):
            return self._save_connected_group(payload, existing=path.endswith("/group"))

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
            status, content_type, body = self._apply_configuration()
            response = json.loads(body.decode("utf-8"))
            response["timer"] = _timer_payload(timer)
            response["live_applied"] = status == HTTPStatus.OK
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
            status, content_type, body = self._apply_configuration()
            response = json.loads(body.decode("utf-8"))
            response["timer_id"] = timer_id
            response["live_applied"] = status == HTTPStatus.OK
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
            status, content_type, body = self._apply_configuration()
            response = json.loads(body.decode("utf-8"))
            response["progression"] = _progression_payload(
                progression,
                repository=self.repository,
                now=int(time.time()),
            )
            response["live_applied"] = status == HTTPStatus.OK
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
            status, content_type, body = self._apply_configuration()
            response = json.loads(body.decode("utf-8"))
            response["progression_id"] = progression_id
            response["live_applied"] = status == HTTPStatus.OK
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

        if path == "/api/service-assignments":
            try:
                assignment = ServiceAssignment(
                    id=payload["id"],
                    device_id=payload["device_id"],
                    name=payload["name"],
                    domains=payload["domains"],
                    profile_id=payload["profile_id"],
                    enabled=payload.get("enabled", True),
                )
                device = self.repository.get_device(assignment.device_id)
                if device is None or device.ipv4_address not in IPv4Network("192.168.50.0/24"):
                    raise ValueError("device must be on the test Wi-Fi")
                if self.repository.get_profile(assignment.profile_id) is None:
                    raise ValueError("profile does not exist")
                if assignment.enabled:
                    other_assignments = tuple(
                        current for current in self.repository.list_service_assignments()
                        if current.enabled and current.id != assignment.id
                    )
                    if (
                        len(assignment.domains)
                        + sum(len(current.domains) for current in other_assignments)
                        > MAX_SERVICE_ASSIGNMENT_DOMAINS_PER_PLAN
                    ):
                        raise ValueError("too many browser service domains")
                    enabled_assignments = len(other_assignments)
                    configured_rules = (
                        len(self.repository.list_devices())
                        + sum(quota.enabled for quota in self.repository.list_service_quotas())
                        + sum(quota.enabled for quota in self.repository.list_time_quotas())
                        + enabled_assignments + 1
                    )
                    if configured_rules > 15:
                        raise ValueError("too many combined device and service rules")
                    existing = (
                        current for current in other_assignments
                        if current.device_id == assignment.device_id
                    )
                    if any(
                        domain == previous or domain.endswith("." + previous)
                        or previous.endswith("." + domain)
                        for current in existing
                        for domain in assignment.domains
                        for previous in current.domains
                    ):
                        raise ValueError("service assignment domains overlap")
                self.repository.save_service_assignment(assignment)
            except (KeyError, TypeError, ValueError, StorageError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service assignment configuration"}
                )
            return self._apply_quota_change(
                "service_assignment",
                _service_assignment_payload(assignment, repository=self.repository),
            )

        if path == "/api/service-assignments/delete":
            assignment_id = payload.get("id")
            if not isinstance(assignment_id, str):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid service assignment id"}
                )
            if not self.repository.delete_service_assignment(assignment_id):
                return HTTPStatus.NOT_FOUND, "application/json", _json_bytes(
                    {"error": "service assignment does not exist"}
                )
            return self._apply_quota_change("deleted_id", assignment_id)

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
            except HelperClientError as error:
                message = safe_router_import_message(str(error))
                LOGGER.warning("router Wi-Fi import failed: %s", message)
                return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                    {"error": message}
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

    def _validate_saved_plan(self) -> None:
        clients = eligible_clients(self.repository)
        eligible_ids = {device.id for device in clients.devices}
        rules = len(eligible_ids) + sum(
            bool(item.enabled and item.device_id in eligible_ids)
            for items in (
                self.repository.list_service_quotas(),
                self.repository.list_time_quotas(),
                self.repository.list_service_assignments(),
            )
            for item in items
        )
        if rules > 15:
            raise PlanError(
                "At most 15 affected devices and service rules can be active. Remove an assignment or keep the device unaffected."
            )
        compile_stored_plan(repository=self.repository)

    def _verify_selected_client(self, device: Device, snapshot: dict) -> None:
        if not snapshot["association_status_available"] or not any(
            client["association_verified"]
            and client["ipv4_address"] == str(device.ipv4_address)
            and client["mac_address"] == device.mac_address
            for client in snapshot["devices"]
        ):
            raise ValueError(
                "The selected client is no longer verified on JitterSim Wi-Fi. Refresh the list."
            )

    def _saved_control_response(self, fields: dict, snapshot=None):
        if self.helper_client is None:
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        **fields,
                        "live_applied": False,
                        "message": "Saved. Connect the network helper to apply this change.",
                    }
                ),
            )
        status, content_type, body = self._apply_configuration_locked(
            connected_snapshot=snapshot, sample_usage=False
        )
        response = json.loads(body)
        response.update(fields)
        response["live_applied"] = status == HTTPStatus.OK
        if status != HTTPStatus.OK:
            response["error"] = (
                "Settings saved but the network update failed. Protection is not confirmed. "
                + response.get("error", "")
            )
        return status, content_type, _json_bytes(response)

    def _change_unaffected_device(self, payload: dict, *, remove: bool):
        operation = self.scheduler.control_operation() if self.scheduler else nullcontext()
        with operation:
            try:
                item = UnaffectedDevice(
                    payload["mac_address"], payload.get("display_name", ""), int(time.time())
                )
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.transaction():
                    if remove:
                        self.repository.delete_unaffected_device(item.mac_address)
                    else:
                        self.repository.save_unaffected_device(item)
                    self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            return self._saved_control_response(
                {
                    "devices": [
                        asdict(device) for device in self.repository.list_unaffected_devices()
                    ]
                }
            )

    def _save_connected_group(self, payload: dict, *, existing: bool):
        operation = self.scheduler.control_operation() if self.scheduler else nullcontext()
        with operation:
            try:
                if existing:
                    group = self.repository.get_device_group(payload["group_id"])
                    if group is None:
                        raise ValueError("The selected group no longer exists")
                    connected = [payload]
                    device_ids = list(group.device_ids)
                else:
                    connected = payload.get("connected_clients", [])
                    device_ids = list(payload.get("device_ids", []))
                    group = None
                if not isinstance(connected, list) or len(connected) > 15:
                    raise ValueError("Select at most 15 connected clients")
                snapshot = station_snapshot(self.helper_client) if connected else None
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.transaction():
                    profile_id = group.profile_id if existing else payload.get("profile_id") or None
                    for client in connected:
                        mac = normalize_exempt_mac(client["mac_address"])
                        saved = self.repository.get_device_by_mac_address(mac)
                        base_profile = saved.profile_id if saved else profile_id
                        if base_profile is None:
                            raise ValueError(
                                "Choose a group profile before adding an unassigned client"
                            )
                        device = _connected_device_from_payload(
                            {**client, "profile_id": base_profile}, self.repository
                        )
                        self._verify_selected_client(device, snapshot)
                        self.repository.save_device(device)
                        if device.id not in device_ids:
                            device_ids.append(device.id)
                    group = DeviceGroup(
                        id=group.id if existing else payload["id"],
                        name=group.name if existing else payload["name"],
                        device_ids=tuple(device_ids),
                        profile_id=profile_id,
                    )
                    for prior in self.repository.list_device_groups():
                        if prior.id != group.id and set(prior.device_ids) & set(group.device_ids):
                            raise ValueError(
                                f"A selected device already belongs to {prior.name}. Remove it from that group first."
                            )
                    self.repository.save_device_group(group)
                    self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            return self._saved_control_response(
                {"group": _device_group_payload(group, repository=self.repository)}, snapshot
            )

    def _base_profile_feedback(self, device: Device) -> str:
        now = int(time.time())
        group = next(
            (item for item in self.repository.list_device_groups() if device.id in item.device_ids),
            None,
        )
        data_limit = self.repository.get_data_limit(device.id)
        group_limit = self.repository.get_group_data_limit(group.id) if group else None
        progression = next(
            (
                item
                for item in self.repository.list_active_progressions(now=now)
                if item.device_id == device.id
            ),
            None,
        )
        timer = next(
            (
                item
                for item in self.repository.list_active_timers(now=now)
                if item.device_id == device.id
            ),
            None,
        )
        schedule = active_schedule(
            schedules=self.repository.list_schedules(),
            device_id=device.id,
            moment=datetime.fromtimestamp(now),
        )
        if data_limit and data_limit.enabled and data_limit.used_bytes >= data_limit.limit_bytes:
            current = f"The device data limit currently uses {data_limit.profile_id}."
        elif (
            group_limit
            and group_limit.enabled
            and group_limit.used_bytes >= group_limit.limit_bytes
        ):
            current = (
                f"The shared data limit for {group.name} currently uses {group_limit.profile_id}."
            )
        elif progression:
            current = f"Progression {progression.id} is currently active."
        elif timer:
            current = f"Timer {timer.id} currently uses {timer.profile_id}."
        elif schedule:
            current = f"Schedule {schedule.id} currently uses {schedule.profile_id}."
        elif group and group.profile_id:
            current = f"Group {group.name} currently uses {group.profile_id}."
        else:
            current = "This is the current device profile."
        return f"Base profile {device.profile_id} saved. " + current

    def _apply_profile_to_connected_device(self, payload: dict):
        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
            )
        operation = self.scheduler.control_operation() if self.scheduler else nullcontext()
        with operation:
            try:
                device = _connected_device_from_payload(payload, self.repository)
                if device.mac_address in {
                    item.mac_address for item in self.repository.list_unaffected_devices()
                }:
                    raise ValueError(
                        "This device is unaffected by profiles. Remove its protection before applying a profile."
                    )
                snapshot = station_snapshot(self.helper_client)
                self._verify_selected_client(device, snapshot)
                if self.scheduler:
                    self.scheduler.sample_before_control_change(repository=self.repository)
                with self.repository.transaction():
                    self.repository.save_device(device)
                    self._validate_saved_plan()
            except (KeyError, TypeError, ValueError, StorageError) as error:
                return HTTPStatus.CONFLICT, "application/json", _json_bytes({"error": str(error)})
            return self._saved_control_response(
                {
                    "device": _device_payload(device),
                    "message": self._base_profile_feedback(device),
                },
                snapshot,
            )

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
        eligible_ids = (
            self.scheduler.counter_device_ids(repository=self.repository)
            if self.scheduler
            else {
                device.id
                for device in eligible_clients(
                    self.repository,
                    station_snapshot(self.helper_client)
                    if self.repository.list_unaffected_devices()
                    else None,
                ).devices
            }
        )
        member_ids = tuple(device_id for device_id in group.device_ids if device_id in eligible_ids)
        try:
            counters = self.helper_client.traffic_counters().get("devices", {})
        except HelperClientError:
            LOGGER.warning("could not read group quota counter before reset", exc_info=True)
            return None
        if not isinstance(counters, dict) or any(
            device_id not in counters for device_id in member_ids
        ):
            return None
        values = [counters[device_id] for device_id in member_ids]
        return (
            sum(values) if all(isinstance(value, int) and value >= 0 for value in values) else None
        )

    def _apply_quota_change(
        self,
        key: str,
        value: Any,
    ) -> Tuple[int, str, bytes]:
        """Return saved settings and apply them when hardware is available."""

        if self.helper_client is None:
            return (
                HTTPStatus.OK,
                "application/json",
                _json_bytes(
                    {
                        key: value,
                        "live_applied": False,
                        "message": "settings saved; apply the configuration in hardware mode to start them",
                    }
                ),
            )
        status, content_type, body = self._apply_configuration()
        response = json.loads(body.decode("utf-8"))
        response[key] = value
        response["live_applied"] = status == HTTPStatus.OK
        return status, content_type, _json_bytes(response)

    def _apply_configuration(self) -> Tuple[int, str, bytes]:
        operation = (
            self.scheduler.control_operation() if self.scheduler is not None else nullcontext()
        )
        with operation:
            return self._apply_configuration_locked()

    def _apply_configuration_locked(
        self, connected_snapshot=None, *, sample_usage=True
    ) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
            )
        if self.scheduler and sample_usage:
            self.scheduler.sample_before_control_change(repository=self.repository)
        try:
            plan = (
                self.scheduler.build_plan(
                    repository=self.repository, connected_snapshot=connected_snapshot
                )
                if self.scheduler is not None
                else compile_stored_plan(
                    repository=self.repository,
                    connected_snapshot=(
                        connected_snapshot
                        if connected_snapshot is not None
                        else station_snapshot(self.helper_client)
                    ),
                )
            )
        except (PlanError, StorageError):
            return (
                HTTPStatus.CONFLICT,
                "application/json",
                _json_bytes({"error": "stored configuration cannot be applied"}),
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
            return (
                HTTPStatus.BAD_GATEWAY,
                "application/json",
                _json_bytes(
                    {
                        "error": "network helper apply failed",
                        "hint": (
                            "On the Pi, run sudo journalctl -u jittersim-helper.service "
                            "-n 80 --no-pager to see which network step failed and "
                            "whether cleanup completed."
                        ),
                        "state": _runtime_state_payload(state),
                    }
                ),
            )
        self.repository.rebase_quota_counters(now=int(time.time()))
        state = self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )
        if self.scheduler is not None:
            self.scheduler.activate(applied_payload=plan_to_payload(plan))
        return (
            HTTPStatus.OK,
            "application/json",
            _json_bytes(
                {
                    "result": result,
                    "state": _runtime_state_payload(state),
                    "unverified_device_ids": list(plan.unverified_device_ids),
                    "warning": (
                        "Profiles were skipped for devices whose current Wi-Fi identity could not be verified: "
                        + ", ".join(plan.unverified_device_ids)
                        if plan.unverified_device_ids
                        else None
                    ),
                }
            ),
        )

    def _reset_network(self) -> Tuple[int, str, bytes]:
        operation = (
            self.scheduler.control_operation() if self.scheduler is not None else nullcontext()
        )
        with operation:
            return self._reset_network_locked()

    def _reset_network_locked(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "application/json",
                _json_bytes({"error": "network helper is not configured"}),
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
        parsed_path = urlsplit(self.path)
        path = parsed_path.path
        if path.startswith("/api/") and path not in PUBLIC_READ_PATHS:
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
            status, content_type, body = self.server.response_for(
                path,
                query=parsed_path.query,
            )
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


def _connected_device_from_payload(payload: Dict[str, Any], repository: ConfigRepository) -> Device:
    """Build an assignment for a visible client while preserving existing names and IDs."""

    ipv4_address = payload["ipv4_address"]
    profile_id = payload["profile_id"]
    mac_address = normalize_exempt_mac(payload["mac_address"])
    if repository.get_profile(profile_id) is None:
        raise ValueError("profile does not exist")

    address_only = Device(
        id="connected-client",
        name="Connected client",
        ipv4_address=ipv4_address,
        profile_id=profile_id,
    )
    existing = repository.get_device_by_mac_address(mac_address)
    occupant = repository.get_device_by_ipv4_address(str(address_only.ipv4_address))
    if occupant is not None and occupant.mac_address not in (None, mac_address):
        raise ValueError(
            "This IP belongs to a different saved device. Update its old IP assignment first."
        )
    if occupant is not None and existing is not None and occupant.id != existing.id:
        raise ValueError("This IP conflicts with another saved assignment")
    existing = existing or occupant
    generated_id = "client-" + mac_address.replace(":", "")
    if existing is None and repository.get_device(generated_id) is not None:
        raise ValueError("The detected client ID conflicts with another saved assignment")
    return Device(
        id=existing.id if existing else generated_id,
        name=existing.name if existing else f"Client {address_only.ipv4_address}",
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
