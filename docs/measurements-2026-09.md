# Measured effect of the profile presets

These tables show that a JitterSim profile changes what a test device
experiences. They were measured on 25 September 2026 with the real `tc` and
`nftables` commands JitterSim applies. The setup was a Linux 6.8 kernel in
Docker, with a router, a Pi and a test client in network namespaces. The test
client ran `jittersim measure` against the lab router, through its own
queues, exactly as a device on the test Wi-Fi does.

This is not the Pi's Wi-Fi. Real Wi-Fi adds its own delay, jitter and loss,
so repeat the measurements on the Pi for the class report (see below).

## Presets

Each run sent 200 pings, 50 ms apart, then downloaded a 500 KB file.

| Run | Target | Sent | Loss | RTT avg | RTT p95 | Jitter | Reordered | Download |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Normal | 192.168.0.1 | 200 | 0.0% | 0.12 ms | 0.16 ms | 0.03 ms | 0 | 627.80 Mbit/s |
| Good 4G | 192.168.0.1 | 200 | 0.0% | 53.17 ms | 59.80 ms | 4.92 ms | 0 | 9.37 Mbit/s |
| 3G | 192.168.0.1 | 200 | 0.0% | 206.44 ms | 234.00 ms | 17.99 ms | 0 | 0.68 Mbit/s |
| Lossy Wi-Fi | 192.168.0.1 | 200 | 11.0% | 15.50 ms | 34.00 ms | 11.01 ms | 0 | 8.85 Mbit/s |
| Satellite | 192.168.0.1 | 200 | 3.5% | 603.67 ms | 629.00 ms | 17.77 ms | 0 | 0.72 Mbit/s |

What the columns show:

- **Round trips** grow by the upload delay plus the download delay. Good 4G
  sets 25 ms each way and added about 53 ms. 3G sets 100 ms each way and
  added about 206 ms. Satellite sets 300 ms each way and added about 604 ms.
- **Jitter** is the mean change between consecutive round trips, a
  different measure from netem's setting. It rises with the configured
  jitter: about 5 ms for Good 4G, which sets 5 ms, and 11 to 18 ms for the
  presets that set 15 to 20 ms.
- **Loss** counts pings without a reply. A ping crosses the upload queue and
  then the download queue, so Lossy Wi-Fi's 5% in each direction loses about
  10% of round trips. Its loss comes in bursts, so runs vary: a second run
  lost 5.0%.
- **Download** is one 500 KB transfer. 3G reaches 0.68 of its 0.78 Mbit/s
  download limit. Good 4G and Satellite stay far below their 30 and
  10 Mbit/s limits, because a short transfer spends most of its time waiting
  for round trips while TCP speeds up. Satellite's 600 ms round trip holds a
  10 Mbit/s link to 0.72 Mbit/s for this file. Normal is limited only by the
  lab's virtual links.

## Downloads vary strongly under loss

Twelve 2 MB downloads with the Lossy Wi-Fi preset took between 0.3 and
22.6 seconds, between 0.7 and 68 Mbit/s. When losses hit in a burst, TCP
waits before it sends again, and each wait is longer than the last. One
download speed is therefore not a fair summary of a lossy profile; compare
several runs.

## Repeat the lab measurements

From the repository, on a Mac or Linux machine with Docker:

```bash
docker build -f tests/kernel/Dockerfile -t jittersim-kernel-tests .
docker run --rm --privileged jittersim-kernel-tests python -m tests.kernel.measure_profiles
```

The kernel tests also check that a measurement follows its profile: with
40 ms of delay each way and 20% upload loss, the measured round trips must
grow by 75 to 95 ms and the loss must stay between 10% and 30%.

## Measure on the Pi for the report

1. Connect a laptop to the JitterSim test Wi-Fi and assign it a profile with
   a normal connection.
2. On the laptop, run `jittersim measure --label normal`. Add
   `--download URL` with a large public file to include a download speed;
   it downloads for at most 20 seconds. Some servers refuse scripted
   downloads, so try another file if it fails with an HTTP error. If
   JitterSim is not installed there, copy `src/jittersim/measure.py` and
   run `python3 measure.py measure --label normal`. It needs only
   Python 3.9 and `ping` on macOS or Linux; Windows ping is not supported.
3. Give the laptop an impaired profile in the dashboard, then run
   `jittersim measure --label 3g`.
4. Run `jittersim compare jittersim-normal.json jittersim-3g.json` for a
   Markdown table. Alternatively, add both files to the dashboard's
   **Measurements** panel, which draws each run's round trips and copies the
   table as Markdown.

The test Wi-Fi can reach only public addresses, so the default target is
`1.1.1.1`. Measure at a quiet time, since other household traffic shares
the Internet connection.
