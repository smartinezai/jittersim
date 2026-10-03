# JitterSim code ownership

Keep changes close to the feature they affect. Moving code is useful when it
reduces the unrelated state a reader must understand.

## Dashboard source

`src/jittersim/api/dashboard.html` owns the page markup. `dashboard.css` owns
its base styles. `dashboard.js` owns the main browser behavior and
`dashboard-navigation.js` owns the existing navigation and card behavior.
The scripts keep their original scope and execution order.

`api/dashboard.py` assembles these resources with the existing workflow,
access, phone, reconnect and trust assets during server import. It may read packaged
presentation resources. It does not depend on the HTTP server, configuration
repository, scheduler or privileged helper.

The assembled page continues to use inline scripts and styles. Source files
are not separate public HTTP endpoints. Package-data must include every
resource used during assembly.

## HTTP and shared state

`api/server.py` owns HTTP admission, response delivery and the page's content
security policy. Its `DASHBOARD_HTML` and
`DASHBOARD_CONTENT_SECURITY_POLICY` remain the inputs used by delivery.
Existing test fixtures may replace them.

Configuration writes still use the existing repository and scheduler owners.
The helper still owns privileged networking. Preserve transaction scope,
operation order, protected-device handling and Panic's priority when moving
request handling in later changes.

## Change review

- Identify which existing area owns the behavior and the state it needs.
- Keep presentation code independent of networking and stored user data.
- Avoid passing a whole server object to a new feature area.
- Keep source extraction separate from changed behavior.
- Check installed resources through a fresh wheel and a clean container when
  package configuration changes.
- Preserve bounded reads, caches, requests and background work.
- Test important failure paths and shared state rather than every wrapper.
- Recheck overlapping changes before publishing or merging.

Later separation of browser features, storage and recovery needs its own plan.
Do not move control ownership merely to reduce a file's line count.
