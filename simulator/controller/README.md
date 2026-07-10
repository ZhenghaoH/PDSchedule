# Controller Bandwidth Simulation

This folder contains an offline simulation for controller-level P/D pair
scheduling. It replays `BurstGPT_1.csv` against the current experimental
topology and mirrors the normalized fitness in
`P2P_FlexPD/scheduler/controller/scheduler_PSO.py`.

## Topology

- `P1` and `D1` are on machine `170`; local bandwidth is `36000 Mbps`.
- `P2` and `D2` are on machine `169`; local bandwidth is `36000 Mbps`.
- `P3` is on machine `167`.
- `D3` is on machine `166`.
- Cross-machine links default to `1000 Mbps`.

Pool capacities are taken from `run_170.sh`, `run_169.sh`, `run_167.sh`, and
`run_166.sh`.

## Usage

Datasets live under `P2P_FlexPD/simulator/dataset`, and result JSON files should
be written under `P2P_FlexPD/simulator/result`.

Run a static affinity simulation:

```bash
python P2P_FlexPD/simulator/controller/controller_bandwidth_simulation.py --scenario static --limit 200000
```

Run with dynamic load using original timestamps:

```bash
python P2P_FlexPD/simulator/controller/controller_bandwidth_simulation.py --scenario dynamic --limit 200000
```

Run a burst simulation that compresses timestamps by `100x`:

```bash
python P2P_FlexPD/simulator/controller/controller_bandwidth_simulation.py --scenario burst --limit 200000
```

Replay requests at a fixed QPS while preserving the BurstGPT token distribution:

```bash
python P2P_FlexPD/simulator/controller/controller_bandwidth_simulation.py \
  --scenario dynamic \
  --limit 200000 \
  --qps 50
```

When `--qps` is set, the script ignores the original `Timestamp` gaps and uses:

```text
arrival_time_s = (request_index - 1) / qps
```

`Request tokens` and `Response tokens` still come from `BurstGPT_1.csv`. In
`static` mode, QPS only changes simulated duration and average bandwidth output;
it does not change pair selection because dynamic loads are disabled.

Write JSON output:

```bash
python P2P_FlexPD/simulator/controller/controller_bandwidth_simulation.py \
  --scenario burst \
  --limit 200000 \
  --output-json P2P_FlexPD/simulator/result/latest_controller_bandwidth_result.json
```

Run the online joint controller/gateway/vLLM continuous batching simulation:

```bash
python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --limit 1000 \
  --qps 10 \
  --output-json P2P_FlexPD/simulator/result/latest_joint_result.json
```

The joint simulation is event-driven. Request arrivals, Prefill batch
completion, KV transfer completion, and Decode generation steps advance on one
timeline. Each arriving request sees the current online Prefill, Decode, and
network state before selecting a P/D pair.

- Prefill uses a `slo_aware_new`-style batch lateness penalty with predicted
  instance availability and chunked prefill batches.
- Decode uses the `slo_tpot_admission` selection rule and step-level continuous
  batching.
- Network load is tracked as bandwidth occupancy:

```text
network_load = current_active_mbps / link_capacity_mbps
```

For example, if a `1000 Mbps` link currently carries `300 Mbps`, the online
network load used by controller fitness is `0.3`.

For the joint simulation, `--scenario dynamic` keeps the original dataset
arrival timeline unless `--qps` is provided. When `--qps` is set, arrivals are
replayed at a fixed interval while the system state still advances online.

Compare controller/gateway baseline policies:

```bash
python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --policy pso_slo \
  --limit 1000 \
  --qps 50

python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --policy round_robin \
  --limit 1000 \
  --qps 50

python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --policy least_load \
  --limit 1000 \
  --qps 50

python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --policy adaptive_variance \
  --limit 1000 \
  --qps 50
```

Policy meanings:

- `pso_slo`: current behavior, controller PSO fitness plus SLO-aware instance
  scheduling.
- `round_robin`: controller cycles through all P-D pairs, and each selected pool
  cycles through its instances.
- `least_load`: controller independently selects the lowest normalized-load P
  pool and D pool. Inside each pool, Prefill follows the gateway
  `lowest_prompt_throughput` behavior and Decode follows
  `lowest_generate_throughput`.
- `adaptive_variance`: controller minimizes a two-part fitness:
  dynamic ability mismatch plus variance-based workload imbalance. Prefill and
  Decode time proportions are estimated from TTFT/TPOT profiles, while network
  ability is ranked from candidate P-D transfer time.

Run all policies in one command and write a PNG comparison figure:

```bash
python P2P_FlexPD/simulator/controller/joint_controller_instance_simulation.py \
  --compare-all \
  --limit 1000 \
  --qps 50 \
  --output-json P2P_FlexPD/simulator/result/latest_joint_comparison.json \
  --figure-output P2P_FlexPD/simulator/result/latest_joint_comparison.png
```

The PNG compares SLO attainment, latency tail, network usage, and instance
utilization across `pso_slo`, `round_robin`, `least_load`, and
`adaptive_variance`.

## Output

The script prints:

- request count and percentage for each `(Prefill, Decode)` pair;
- simulation mode and event counts for the online joint simulation;
- KV transfer volume per pair;
- local versus cross-machine request percentage;
- estimated average cross-machine bandwidth usage and utilization.
- arrival mode and QPS when fixed-QPS replay is enabled.

The controller bandwidth script remains a lightweight offline analysis. The
joint controller/instance script is online event-driven, but still abstracts
away lower-level details such as KV block allocation, kernel overlap, and
paged-attention memory fragmentation.
