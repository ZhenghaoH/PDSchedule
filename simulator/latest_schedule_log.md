# Simulator Schedule Log

## Summary

- seed: 0
- num_requests: 218
- arrival: {'mode': 'qps', 'qps': 20}
- prompt_tokens: {'source': 'burstgpt', 'csv_path': 'C:\\Users\\19229\\Nutstore\\1\\研\\代码\\PD分离推理调度代码\\PDSchedule\\FlexPD\\simulator\\BurstGPT_1.csv', 'loaded_rows': 218}
- slo: {'min_ttft_ms': 1500.0, 'max_ttft_ms': 1500.0}
- strategy: pso
- summary: {'strategy': 'pso', 'total_requests': 218, 'met_slo_requests': 56, 'missed_slo_requests': 162, 'slo_attainment_rate': 0.25688073394495414, 'total_prompt_tokens': 139278, 'goodput_tokens': 35122, 'goodput_ratio': 0.2521719151624808, 'avg_ttft_ms': 3874.3778986776892, 'max_ttft_ms': 20278.98050715688}
- comparison: {'goodput_tokens_delta': 24041, 'goodput_ratio_delta': 0.17261161130975455, 'met_slo_requests_delta': 36, 'slo_attainment_rate_delta': 0.1651376146788991, 'avg_ttft_ms_delta': -2970.3089769559588}

## PSO Rolling Dispatch

### Dispatch 1 - running

- dispatch_time_ms: 250.506
- planned_start_ms: 250.506
- planned_finish_ms: 365.873
- actual_start_ms: 250.506
- actual_finish_ms: 365.873
- total_tokens: 472
- request_ids: req-001

### Dispatch 2 - waiting

- dispatch_time_ms: 258.917
- planned_start_ms: 365.873
- planned_finish_ms: 573.677
- actual_start_ms: 365.873
- actual_finish_ms: 840.767
- total_tokens: 1087
- request_ids: req-002

### Dispatch 3 - waiting

- dispatch_time_ms: 281.838
- planned_start_ms: 365.873
- planned_finish_ms: 636.354
- actual_start_ms: 365.873
- actual_finish_ms: 840.767
- total_tokens: 417
- request_ids: req-003

### Dispatch 4 - waiting

- dispatch_time_ms: 303.313
- planned_start_ms: 365.873
- planned_finish_ms: 840.767
- actual_start_ms: 365.873
- actual_finish_ms: 840.767
- total_tokens: 1360
- request_ids: req-004

### Dispatch 5 - waiting

- dispatch_time_ms: 404.934
- planned_start_ms: 840.767
- planned_finish_ms: 912.997
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 185
- request_ids: req-005

### Dispatch 6 - waiting

- dispatch_time_ms: 420.572
- planned_start_ms: 840.767
- planned_finish_ms: 1045.498
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 586
- request_ids: req-006

### Dispatch 7 - waiting

- dispatch_time_ms: 476.597
- planned_start_ms: 840.767
- planned_finish_ms: 1051.060
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 37
- request_ids: req-007

### Dispatch 8 - waiting

- dispatch_time_ms: 504.687
- planned_start_ms: 840.767
- planned_finish_ms: 1059.176
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 54
- request_ids: req-008

### Dispatch 9 - waiting

- dispatch_time_ms: 511.275
- planned_start_ms: 840.767
- planned_finish_ms: 1288.841
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 1528
- request_ids: req-009

### Dispatch 10 - waiting

- dispatch_time_ms: 583.382
- planned_start_ms: 840.767
- planned_finish_ms: 1302.218
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 89
- request_ids: req-010

### Dispatch 11 - waiting

- dispatch_time_ms: 618.369
- planned_start_ms: 840.767
- planned_finish_ms: 1384.735
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 549
- request_ids: req-011

### Dispatch 12 - waiting

- dispatch_time_ms: 755.804
- planned_start_ms: 840.767
- planned_finish_ms: 1410.587
- actual_start_ms: 840.767
- actual_finish_ms: 1500.836
- total_tokens: 172
- request_ids: req-012

### Dispatch 13 - waiting

- dispatch_time_ms: 757.954
- planned_start_ms: 840.767
- planned_finish_ms: 1556.231
- actual_start_ms: 840.767
- actual_finish_ms: 2160.905
- total_tokens: 969
- request_ids: req-013

### Dispatch 14 - waiting

- dispatch_time_ms: 840.767
- planned_start_ms: 840.767
- planned_finish_ms: 1807.390
- actual_start_ms: 1500.836
- actual_finish_ms: 2820.973
- total_tokens: 1671
- request_ids: req-014, req-015

### Dispatch 15 - waiting

- dispatch_time_ms: 844.422
- planned_start_ms: 1500.836
- planned_finish_ms: 1845.416
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 253
- request_ids: req-016

### Dispatch 16 - waiting

- dispatch_time_ms: 902.166
- planned_start_ms: 1500.836
- planned_finish_ms: 1848.873
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 23
- request_ids: req-017

### Dispatch 17 - waiting

- dispatch_time_ms: 908.113
- planned_start_ms: 1500.836
- planned_finish_ms: 1925.378
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 509
- request_ids: req-018

### Dispatch 18 - waiting

- dispatch_time_ms: 909.746
- planned_start_ms: 1500.836
- planned_finish_ms: 1983.546
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 387
- request_ids: req-019

### Dispatch 19 - waiting

- dispatch_time_ms: 982.785
- planned_start_ms: 1500.836
- planned_finish_ms: 2093.719
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 733
- request_ids: req-020

### Dispatch 20 - waiting

- dispatch_time_ms: 1014.042
- planned_start_ms: 1500.836
- planned_finish_ms: 2334.289
- actual_start_ms: 1500.836
- actual_finish_ms: 2160.905
- total_tokens: 1305
- request_ids: req-021

### Dispatch 21 - waiting

- dispatch_time_ms: 1500.836
- planned_start_ms: 1500.836
- planned_finish_ms: 3104.530
- actual_start_ms: 2160.905
- actual_finish_ms: 3481.042
- total_tokens: 3502
- request_ids: req-022, req-028, req-027, req-025, req-026, req-023

### Dispatch 22 - waiting

- dispatch_time_ms: 2160.905
- planned_start_ms: 2160.905
- planned_finish_ms: 3481.042
- actual_start_ms: 2820.973
- actual_finish_ms: 3481.042
- total_tokens: 3756
- request_ids: req-043, req-041, req-036, req-042

### Dispatch 23 - waiting

- dispatch_time_ms: 2160.905
- planned_start_ms: 2820.973
- planned_finish_ms: 4141.111
- actual_start_ms: 2820.973
- actual_finish_ms: 4141.111
- total_tokens: 507
- request_ids: req-029

### Dispatch 24 - waiting

- dispatch_time_ms: 2820.973
- planned_start_ms: 2820.973
- planned_finish_ms: 4141.111
- actual_start_ms: 3481.042
- actual_finish_ms: 4141.111
- total_tokens: 3640
- request_ids: req-053, req-044, req-056, req-054, req-049, req-050, req-051, req-055, req-030, req-052, req-045

### Dispatch 25 - waiting

- dispatch_time_ms: 2820.973
- planned_start_ms: 3481.042
- planned_finish_ms: 4797.422
- actual_start_ms: 3481.042
- actual_finish_ms: 4801.179
- total_tokens: 614
- request_ids: req-039

### Dispatch 26 - waiting

- dispatch_time_ms: 3481.042
- planned_start_ms: 3481.042
- planned_finish_ms: 4801.179
- actual_start_ms: 4141.111
- actual_finish_ms: 4801.179
- total_tokens: 3387
- request_ids: req-065, req-066

### Dispatch 27 - waiting

- dispatch_time_ms: 3481.042
- planned_start_ms: 4141.111
- planned_finish_ms: 4801.179
- actual_start_ms: 4141.111
- actual_finish_ms: 4801.179
- total_tokens: 49
- request_ids: req-059, req-067

### Dispatch 28 - waiting

- dispatch_time_ms: 3481.042
- planned_start_ms: 4141.111
- planned_finish_ms: 5461.248
- actual_start_ms: 4141.111
- actual_finish_ms: 5461.248
- total_tokens: 103
- request_ids: req-061

### Dispatch 29 - waiting

- dispatch_time_ms: 4141.111
- planned_start_ms: 4141.111
- planned_finish_ms: 5461.248
- actual_start_ms: 4801.179
- actual_finish_ms: 5461.248
- total_tokens: 4029
- request_ids: req-079, req-046, req-063, req-078, req-034, req-073, req-072, req-069, req-081, req-058

### Dispatch 30 - waiting

- dispatch_time_ms: 4141.111
- planned_start_ms: 4801.179
- planned_finish_ms: 6116.056
- actual_start_ms: 4801.179
- actual_finish_ms: 6121.317
- total_tokens: 540
- request_ids: req-076

### Dispatch 31 - waiting

- dispatch_time_ms: 4801.179
- planned_start_ms: 4801.179
- planned_finish_ms: 6781.385
- actual_start_ms: 5461.248
- actual_finish_ms: 6781.385
- total_tokens: 3805
- request_ids: req-087, req-086, req-082, req-088, req-089, req-090, req-091, req-083, req-092, req-060, req-080

### Dispatch 32 - waiting

- dispatch_time_ms: 5461.248
- planned_start_ms: 5461.248
- planned_finish_ms: 7441.454
- actual_start_ms: 6121.317
- actual_finish_ms: 7441.454
- total_tokens: 4074
- request_ids: req-103, req-098, req-099, req-102, req-100, req-104, req-106, req-107, req-108, req-105, req-101, req-093, req-094

### Dispatch 33 - waiting

- dispatch_time_ms: 6121.317
- planned_start_ms: 6121.317
- planned_finish_ms: 7441.454
- actual_start_ms: 6781.385
- actual_finish_ms: 7441.454
- total_tokens: 3741
- request_ids: req-123, req-110, req-122, req-112, req-109, req-118, req-119, req-114, req-120, req-121, req-124

### Dispatch 34 - waiting

- dispatch_time_ms: 6121.317
- planned_start_ms: 6781.385
- planned_finish_ms: 8077.023
- actual_start_ms: 6781.385
- actual_finish_ms: 8101.523
- total_tokens: 587
- request_ids: req-116

### Dispatch 35 - waiting

- dispatch_time_ms: 6781.385
- planned_start_ms: 6781.385
- planned_finish_ms: 8101.523
- actual_start_ms: 7441.454
- actual_finish_ms: 8101.523
- total_tokens: 3649
- request_ids: req-136, req-038, req-115

### Dispatch 36 - waiting

- dispatch_time_ms: 6781.385
- planned_start_ms: 7441.454
- planned_finish_ms: 8101.523
- actual_start_ms: 7441.454
- actual_finish_ms: 8101.523
- total_tokens: 14
- request_ids: req-126

### Dispatch 37 - waiting

- dispatch_time_ms: 6781.385
- planned_start_ms: 7441.454
- planned_finish_ms: 8761.591
- actual_start_ms: 7441.454
- actual_finish_ms: 8761.591
- total_tokens: 447
- request_ids: req-128

### Dispatch 38 - waiting

- dispatch_time_ms: 7441.454
- planned_start_ms: 7441.454
- planned_finish_ms: 8761.591
- actual_start_ms: 8101.523
- actual_finish_ms: 8761.591
- total_tokens: 3640
- request_ids: req-144, req-047, req-141, req-140, req-145, req-142, req-143, req-149, req-127

### Dispatch 39 - waiting

- dispatch_time_ms: 7441.454
- planned_start_ms: 8101.523
- planned_finish_ms: 9418.954
- actual_start_ms: 8101.523
- actual_finish_ms: 9421.660
- total_tokens: 68
- request_ids: req-125

### Dispatch 40 - waiting

- dispatch_time_ms: 8101.523
- planned_start_ms: 8101.523
- planned_finish_ms: 9421.660
- actual_start_ms: 8761.591
- actual_finish_ms: 9421.660
- total_tokens: 3641
- request_ids: req-158, req-160, req-153, req-162, req-154, req-151, req-161, req-074

### Dispatch 41 - waiting

- dispatch_time_ms: 8101.523
- planned_start_ms: 8761.591
- planned_finish_ms: 9421.660
- actual_start_ms: 8761.591
- actual_finish_ms: 9421.660
- total_tokens: 372
- request_ids: req-156, req-157, req-152, req-159, req-139

### Dispatch 42 - waiting

- dispatch_time_ms: 8101.523
- planned_start_ms: 8761.591
- planned_finish_ms: 10081.729
- actual_start_ms: 8761.591
- actual_finish_ms: 10081.729
- total_tokens: 395
- request_ids: req-146

### Dispatch 43 - waiting

- dispatch_time_ms: 8761.591
- planned_start_ms: 8761.591
- planned_finish_ms: 10741.797
- actual_start_ms: 9421.660
- actual_finish_ms: 10741.797
- total_tokens: 3818
- request_ids: req-166, req-163, req-176, req-170, req-175, req-167, req-174, req-172, req-168, req-171, req-164, req-177, req-165, req-155, req-084, req-129

### Dispatch 44 - waiting

- dispatch_time_ms: 9421.660
- planned_start_ms: 9421.660
- planned_finish_ms: 10741.797
- actual_start_ms: 10081.729
- actual_finish_ms: 10741.797
- total_tokens: 3550
- request_ids: req-186, req-188, req-183, req-178, req-190, req-185, req-189, req-187, req-191

### Dispatch 45 - waiting

- dispatch_time_ms: 9421.660
- planned_start_ms: 10081.729
- planned_finish_ms: 11401.866
- actual_start_ms: 10081.729
- actual_finish_ms: 11401.866
- total_tokens: 708
- request_ids: req-179

### Dispatch 46 - waiting

- dispatch_time_ms: 10081.729
- planned_start_ms: 10081.729
- planned_finish_ms: 12061.935
- actual_start_ms: 10741.797
- actual_finish_ms: 12061.935
- total_tokens: 4037
- request_ids: req-194, req-195, req-199, req-200, req-198, req-192, req-148, req-097, req-193

### Dispatch 47 - waiting

- dispatch_time_ms: 10741.797
- planned_start_ms: 10741.797
- planned_finish_ms: 12061.935
- actual_start_ms: 11401.866
- actual_finish_ms: 12061.935
- total_tokens: 3431
- request_ids: req-208, req-202, req-201, req-212, req-205, req-113, req-096

### Dispatch 48 - waiting

- dispatch_time_ms: 10741.797
- planned_start_ms: 11401.866
- planned_finish_ms: 12722.003
- actual_start_ms: 11401.866
- actual_finish_ms: 12722.003
- total_tokens: 930
- request_ids: req-207

### Dispatch 49 - waiting

- dispatch_time_ms: 11401.866
- planned_start_ms: 11401.866
- planned_finish_ms: 12722.003
- actual_start_ms: 12061.935
- actual_finish_ms: 12722.003
- total_tokens: 3520
- request_ids: req-213, req-217, req-216, req-215, req-111, req-206

### Dispatch 50 - waiting

- dispatch_time_ms: 11401.866
- planned_start_ms: 12061.935
- planned_finish_ms: 13363.434
- actual_start_ms: 12061.935
- actual_finish_ms: 13382.072
- total_tokens: 963
- request_ids: req-180

### Dispatch 51 - waiting

- dispatch_time_ms: 12061.935
- planned_start_ms: 12061.935
- planned_finish_ms: 14042.141
- actual_start_ms: 12722.003
- actual_finish_ms: 14042.141
- total_tokens: 4065
- request_ids: req-204, req-033, req-095, req-182

### Dispatch 52 - waiting

- dispatch_time_ms: 12722.003
- planned_start_ms: 12722.003
- planned_finish_ms: 14702.209
- actual_start_ms: 13382.072
- actual_finish_ms: 14702.209
- total_tokens: 3332
- request_ids: req-062, req-130, req-085

### Dispatch 53 - waiting

- dispatch_time_ms: 13382.072
- planned_start_ms: 13382.072
- planned_finish_ms: 14702.209
- actual_start_ms: 14042.141
- actual_finish_ms: 14702.209
- total_tokens: 3664
- request_ids: req-048, req-209, req-150

### Dispatch 54 - waiting

- dispatch_time_ms: 13382.072
- planned_start_ms: 14042.141
- planned_finish_ms: 15303.960
- actual_start_ms: 14042.141
- actual_finish_ms: 15362.278
- total_tokens: 1113
- request_ids: req-138

### Dispatch 55 - waiting

- dispatch_time_ms: 14042.141
- planned_start_ms: 14042.141
- planned_finish_ms: 16022.346
- actual_start_ms: 14702.209
- actual_finish_ms: 16022.346
- total_tokens: 3813
- request_ids: req-197, req-147, req-024

### Dispatch 56 - waiting

- dispatch_time_ms: 14702.209
- planned_start_ms: 14702.209
- planned_finish_ms: 16682.415
- actual_start_ms: 15362.278
- actual_finish_ms: 16682.415
- total_tokens: 3902
- request_ids: req-064, req-131, req-184

### Dispatch 57 - waiting

- dispatch_time_ms: 15362.278
- planned_start_ms: 15362.278
- planned_finish_ms: 17342.484
- actual_start_ms: 16022.346
- actual_finish_ms: 17342.484
- total_tokens: 4025
- request_ids: req-181, req-057, req-137

### Dispatch 58 - waiting

- dispatch_time_ms: 16022.346
- planned_start_ms: 16022.346
- planned_finish_ms: 17342.484
- actual_start_ms: 16682.415
- actual_finish_ms: 17342.484
- total_tokens: 2934
- request_ids: req-132, req-035

### Dispatch 59 - waiting

- dispatch_time_ms: 16022.346
- planned_start_ms: 16682.415
- planned_finish_ms: 17854.503
- actual_start_ms: 16682.415
- actual_finish_ms: 18002.552
- total_tokens: 1435
- request_ids: req-134

### Dispatch 60 - waiting

- dispatch_time_ms: 16682.415
- planned_start_ms: 16682.415
- planned_finish_ms: 18002.552
- actual_start_ms: 17342.484
- actual_finish_ms: 18002.552
- total_tokens: 3056
- request_ids: req-210, req-031

### Dispatch 61 - waiting

- dispatch_time_ms: 16682.415
- planned_start_ms: 17342.484
- planned_finish_ms: 18662.621
- actual_start_ms: 17342.484
- actual_finish_ms: 18662.621
- total_tokens: 1416
- request_ids: req-173

### Dispatch 62 - waiting

- dispatch_time_ms: 17342.484
- planned_start_ms: 17342.484
- planned_finish_ms: 18662.621
- actual_start_ms: 18002.552
- actual_finish_ms: 18662.621
- total_tokens: 3079
- request_ids: req-117, req-203

### Dispatch 63 - waiting

- dispatch_time_ms: 17342.484
- planned_start_ms: 18002.552
- planned_finish_ms: 19293.982
- actual_start_ms: 18002.552
- actual_finish_ms: 19322.690
- total_tokens: 1631
- request_ids: req-075

### Dispatch 64 - waiting

- dispatch_time_ms: 18002.552
- planned_start_ms: 18002.552
- planned_finish_ms: 19982.758
- actual_start_ms: 18662.621
- actual_finish_ms: 19982.758
- total_tokens: 2914
- request_ids: req-133, req-218

### Dispatch 65 - waiting

- dispatch_time_ms: 18662.621
- planned_start_ms: 18662.621
- planned_finish_ms: 19982.758
- actual_start_ms: 19322.690
- actual_finish_ms: 19982.758
- total_tokens: 3277
- request_ids: req-196, req-070

### Dispatch 66 - waiting

- dispatch_time_ms: 18662.621
- planned_start_ms: 19322.690
- planned_finish_ms: 20558.507
- actual_start_ms: 19322.690
- actual_finish_ms: 20642.827
- total_tokens: 1960
- request_ids: req-040

### Dispatch 67 - waiting

- dispatch_time_ms: 19322.690
- planned_start_ms: 19322.690
- planned_finish_ms: 21302.896
- actual_start_ms: 19982.758
- actual_finish_ms: 21302.896
- total_tokens: 3257
- request_ids: req-068, req-135

### Dispatch 68 - waiting

- dispatch_time_ms: 19982.758
- planned_start_ms: 19982.758
- planned_finish_ms: 21302.896
- actual_start_ms: 20642.827
- actual_finish_ms: 21302.896
- total_tokens: 3433
- request_ids: req-214, req-037

### Dispatch 69 - waiting

- dispatch_time_ms: 19982.758
- planned_start_ms: 20642.827
- planned_finish_ms: 21962.964
- actual_start_ms: 20642.827
- actual_finish_ms: 21962.964
- total_tokens: 1840
- request_ids: req-032

### Dispatch 70 - waiting

- dispatch_time_ms: 20642.827
- planned_start_ms: 20642.827
- planned_finish_ms: 22623.033
- actual_start_ms: 21302.896
- actual_finish_ms: 22623.033
- total_tokens: 3498
- request_ids: req-071, req-077

### Dispatch 71 - waiting

- dispatch_time_ms: 21302.896
- planned_start_ms: 21302.896
- planned_finish_ms: 22783.792
- actual_start_ms: 21962.964
- actual_finish_ms: 22783.792
- total_tokens: 3731
- request_ids: req-169, req-211

## PSO Request Outcomes

- req-001: arrival=250.506ms, dispatch=250.506ms, tokens=472, slo=1500.000ms, complete=365.873ms, latency=115.367ms, met_slo=True
- req-002: arrival=258.917ms, dispatch=258.917ms, tokens=1087, slo=1500.000ms, complete=840.767ms, latency=581.851ms, met_slo=True
- req-003: arrival=281.838ms, dispatch=281.838ms, tokens=417, slo=1500.000ms, complete=840.767ms, latency=558.929ms, met_slo=True
- req-004: arrival=303.313ms, dispatch=303.313ms, tokens=1360, slo=1500.000ms, complete=840.767ms, latency=537.455ms, met_slo=True
- req-005: arrival=404.934ms, dispatch=404.934ms, tokens=185, slo=1500.000ms, complete=1500.836ms, latency=1095.902ms, met_slo=True
- req-006: arrival=420.572ms, dispatch=420.572ms, tokens=586, slo=1500.000ms, complete=1500.836ms, latency=1080.264ms, met_slo=True
- req-007: arrival=476.597ms, dispatch=476.597ms, tokens=37, slo=1500.000ms, complete=1500.836ms, latency=1024.239ms, met_slo=True
- req-008: arrival=504.687ms, dispatch=504.687ms, tokens=54, slo=1500.000ms, complete=1500.836ms, latency=996.149ms, met_slo=True
- req-009: arrival=511.275ms, dispatch=511.275ms, tokens=1528, slo=1500.000ms, complete=1500.836ms, latency=989.561ms, met_slo=True
- req-010: arrival=583.382ms, dispatch=583.382ms, tokens=89, slo=1500.000ms, complete=1500.836ms, latency=917.454ms, met_slo=True
- req-011: arrival=618.369ms, dispatch=618.369ms, tokens=549, slo=1500.000ms, complete=1500.836ms, latency=882.467ms, met_slo=True
- req-012: arrival=755.804ms, dispatch=755.804ms, tokens=172, slo=1500.000ms, complete=1500.836ms, latency=745.032ms, met_slo=True
- req-013: arrival=757.954ms, dispatch=757.954ms, tokens=969, slo=1500.000ms, complete=2160.905ms, latency=1402.950ms, met_slo=True
- req-014: arrival=783.799ms, dispatch=840.767ms, tokens=97, slo=1500.000ms, complete=2160.905ms, latency=1377.106ms, met_slo=True
- req-015: arrival=810.217ms, dispatch=840.767ms, tokens=1574, slo=1500.000ms, complete=2820.973ms, latency=2010.756ms, met_slo=False
- req-016: arrival=844.422ms, dispatch=844.422ms, tokens=253, slo=1500.000ms, complete=2160.905ms, latency=1316.483ms, met_slo=True
- req-017: arrival=902.166ms, dispatch=902.166ms, tokens=23, slo=1500.000ms, complete=2160.905ms, latency=1258.739ms, met_slo=True
- req-018: arrival=908.113ms, dispatch=908.113ms, tokens=509, slo=1500.000ms, complete=2160.905ms, latency=1252.792ms, met_slo=True
- req-019: arrival=909.746ms, dispatch=909.746ms, tokens=387, slo=1500.000ms, complete=2160.905ms, latency=1251.158ms, met_slo=True
- req-020: arrival=982.785ms, dispatch=982.785ms, tokens=733, slo=1500.000ms, complete=2160.905ms, latency=1178.119ms, met_slo=True
- req-021: arrival=1014.042ms, dispatch=1014.042ms, tokens=1305, slo=1500.000ms, complete=2160.905ms, latency=1146.863ms, met_slo=True
- req-022: arrival=1100.701ms, dispatch=1500.836ms, tokens=17, slo=1500.000ms, complete=2820.973ms, latency=1720.272ms, met_slo=False
- req-023: arrival=1260.492ms, dispatch=1500.836ms, tokens=601, slo=1500.000ms, complete=3481.042ms, latency=2220.550ms, met_slo=False
- req-024: arrival=1310.148ms, dispatch=14042.141ms, tokens=1327, slo=1500.000ms, complete=16022.346ms, latency=14712.199ms, met_slo=False
- req-025: arrival=1398.824ms, dispatch=1500.836ms, tokens=1437, slo=1500.000ms, complete=2820.973ms, latency=1422.150ms, met_slo=True
- req-026: arrival=1434.172ms, dispatch=1500.836ms, tokens=1336, slo=1500.000ms, complete=2820.973ms, latency=1386.801ms, met_slo=True
- req-027: arrival=1472.143ms, dispatch=1500.836ms, tokens=19, slo=1500.000ms, complete=2820.973ms, latency=1348.831ms, met_slo=True
- req-028: arrival=1477.010ms, dispatch=1500.836ms, tokens=92, slo=1500.000ms, complete=2820.973ms, latency=1343.963ms, met_slo=True
- req-029: arrival=1548.699ms, dispatch=2160.905ms, tokens=507, slo=1500.000ms, complete=4141.111ms, latency=2592.411ms, met_slo=False
- req-030: arrival=1610.887ms, dispatch=2820.973ms, tokens=938, slo=1500.000ms, complete=4141.111ms, latency=2530.224ms, met_slo=False
- req-031: arrival=1668.153ms, dispatch=16682.415ms, tokens=1544, slo=1500.000ms, complete=18002.552ms, latency=16334.399ms, met_slo=False
- req-032: arrival=1683.984ms, dispatch=19982.758ms, tokens=1840, slo=1500.000ms, complete=21962.964ms, latency=20278.981ms, met_slo=False
- req-033: arrival=1719.705ms, dispatch=12061.935ms, tokens=991, slo=1500.000ms, complete=13382.072ms, latency=11662.367ms, met_slo=False
- req-034: arrival=1729.832ms, dispatch=4141.111ms, tokens=1109, slo=1500.000ms, complete=5461.248ms, latency=3731.416ms, met_slo=False
- req-035: arrival=1805.028ms, dispatch=16022.346ms, tokens=1476, slo=1500.000ms, complete=17342.484ms, latency=15537.456ms, met_slo=False
- req-036: arrival=1824.845ms, dispatch=2160.905ms, tokens=14, slo=1500.000ms, complete=3481.042ms, latency=1656.197ms, met_slo=False
- req-037: arrival=1865.310ms, dispatch=19982.758ms, tokens=1741, slo=1500.000ms, complete=21302.896ms, latency=19437.586ms, met_slo=False
- req-038: arrival=1898.838ms, dispatch=6781.385ms, tokens=1306, slo=1500.000ms, complete=8101.523ms, latency=6202.684ms, met_slo=False
- req-039: arrival=1913.011ms, dispatch=2820.973ms, tokens=614, slo=1500.000ms, complete=4801.179ms, latency=2888.168ms, met_slo=False
- req-040: arrival=1966.606ms, dispatch=18662.621ms, tokens=1960, slo=1500.000ms, complete=20642.827ms, latency=18676.221ms, met_slo=False
- req-041: arrival=2001.143ms, dispatch=2160.905ms, tokens=1698, slo=1500.000ms, complete=3481.042ms, latency=1479.899ms, met_slo=True
- req-042: arrival=2080.446ms, dispatch=2160.905ms, tokens=1907, slo=1500.000ms, complete=3481.042ms, latency=1400.596ms, met_slo=True
- req-043: arrival=2109.058ms, dispatch=2160.905ms, tokens=137, slo=1500.000ms, complete=3481.042ms, latency=1371.984ms, met_slo=True
- req-044: arrival=2191.067ms, dispatch=2820.973ms, tokens=20, slo=1500.000ms, complete=4141.111ms, latency=1950.043ms, met_slo=False
- req-045: arrival=2238.616ms, dispatch=2820.973ms, tokens=127, slo=1500.000ms, complete=4141.111ms, latency=1902.495ms, met_slo=False
- req-046: arrival=2243.911ms, dispatch=4141.111ms, tokens=665, slo=1500.000ms, complete=5461.248ms, latency=3217.337ms, met_slo=False
- req-047: arrival=2320.055ms, dispatch=7441.454ms, tokens=855, slo=1500.000ms, complete=8761.591ms, latency=6441.537ms, met_slo=False
- req-048: arrival=2325.204ms, dispatch=13382.072ms, tokens=1191, slo=1500.000ms, complete=14702.209ms, latency=12377.005ms, met_slo=False
- req-049: arrival=2447.970ms, dispatch=2820.973ms, tokens=18, slo=1500.000ms, complete=4141.111ms, latency=1693.141ms, met_slo=False
- req-050: arrival=2493.578ms, dispatch=2820.973ms, tokens=101, slo=1500.000ms, complete=4141.111ms, latency=1647.533ms, met_slo=False
- req-051: arrival=2507.941ms, dispatch=2820.973ms, tokens=112, slo=1500.000ms, complete=4141.111ms, latency=1633.170ms, met_slo=False
- req-052: arrival=2547.441ms, dispatch=2820.973ms, tokens=264, slo=1500.000ms, complete=4141.111ms, latency=1593.670ms, met_slo=False
- req-053: arrival=2551.267ms, dispatch=2820.973ms, tokens=82, slo=1500.000ms, complete=4141.111ms, latency=1589.843ms, met_slo=False
- req-054: arrival=2567.511ms, dispatch=2820.973ms, tokens=506, slo=1500.000ms, complete=4141.111ms, latency=1573.600ms, met_slo=False
- req-055: arrival=2706.561ms, dispatch=2820.973ms, tokens=530, slo=1500.000ms, complete=4141.111ms, latency=1434.549ms, met_slo=True
- req-056: arrival=2803.179ms, dispatch=2820.973ms, tokens=942, slo=1500.000ms, complete=4141.111ms, latency=1337.931ms, met_slo=True
- req-057: arrival=2867.603ms, dispatch=15362.278ms, tokens=1364, slo=1500.000ms, complete=16682.415ms, latency=13814.812ms, met_slo=False
- req-058: arrival=2870.471ms, dispatch=4141.111ms, tokens=122, slo=1500.000ms, complete=5461.248ms, latency=2590.777ms, met_slo=False
- req-059: arrival=2932.834ms, dispatch=3481.042ms, tokens=24, slo=1500.000ms, complete=4801.179ms, latency=1868.345ms, met_slo=False
- req-060: arrival=2967.540ms, dispatch=4801.179ms, tokens=698, slo=1500.000ms, complete=6121.317ms, latency=3153.776ms, met_slo=False
- req-061: arrival=3089.824ms, dispatch=3481.042ms, tokens=103, slo=1500.000ms, complete=5461.248ms, latency=2371.424ms, met_slo=False
- req-062: arrival=3186.730ms, dispatch=12722.003ms, tokens=1073, slo=1500.000ms, complete=14042.141ms, latency=10855.411ms, met_slo=False
- req-063: arrival=3189.391ms, dispatch=4141.111ms, tokens=200, slo=1500.000ms, complete=5461.248ms, latency=2271.857ms, met_slo=False
- req-064: arrival=3290.330ms, dispatch=14702.209ms, tokens=1282, slo=1500.000ms, complete=16022.346ms, latency=12732.017ms, met_slo=False
- req-065: arrival=3384.901ms, dispatch=3481.042ms, tokens=1762, slo=1500.000ms, complete=4801.179ms, latency=1416.278ms, met_slo=True
- req-066: arrival=3444.989ms, dispatch=3481.042ms, tokens=1625, slo=1500.000ms, complete=4801.179ms, latency=1356.190ms, met_slo=True
- req-067: arrival=3476.531ms, dispatch=3481.042ms, tokens=25, slo=1500.000ms, complete=4801.179ms, latency=1324.648ms, met_slo=True
- req-068: arrival=3540.284ms, dispatch=19322.690ms, tokens=1639, slo=1500.000ms, complete=20642.827ms, latency=17102.544ms, met_slo=False
- req-069: arrival=3575.651ms, dispatch=4141.111ms, tokens=42, slo=1500.000ms, complete=5461.248ms, latency=1885.597ms, met_slo=False
- req-070: arrival=3587.617ms, dispatch=18662.621ms, tokens=1637, slo=1500.000ms, complete=19982.758ms, latency=16395.141ms, met_slo=False
- req-071: arrival=3596.287ms, dispatch=20642.827ms, tokens=1701, slo=1500.000ms, complete=21962.964ms, latency=18366.678ms, met_slo=False
- req-072: arrival=3603.186ms, dispatch=4141.111ms, tokens=383, slo=1500.000ms, complete=5461.248ms, latency=1858.062ms, met_slo=False
- req-073: arrival=3612.773ms, dispatch=4141.111ms, tokens=266, slo=1500.000ms, complete=5461.248ms, latency=1848.475ms, met_slo=False
- req-074: arrival=3656.659ms, dispatch=8101.523ms, tokens=1450, slo=1500.000ms, complete=9421.660ms, latency=5765.000ms, met_slo=False
- req-075: arrival=3757.604ms, dispatch=17342.484ms, tokens=1631, slo=1500.000ms, complete=19322.690ms, latency=15565.086ms, met_slo=False
- req-076: arrival=3814.467ms, dispatch=4141.111ms, tokens=540, slo=1500.000ms, complete=6121.317ms, latency=2306.850ms, met_slo=False
- req-077: arrival=3842.460ms, dispatch=20642.827ms, tokens=1797, slo=1500.000ms, complete=22623.033ms, latency=18780.573ms, met_slo=False
- req-078: arrival=3876.770ms, dispatch=4141.111ms, tokens=20, slo=1500.000ms, complete=5461.248ms, latency=1584.478ms, met_slo=False
- req-079: arrival=3923.381ms, dispatch=4141.111ms, tokens=352, slo=1500.000ms, complete=5461.248ms, latency=1537.867ms, met_slo=False
- req-080: arrival=3963.839ms, dispatch=4801.179ms, tokens=726, slo=1500.000ms, complete=6781.385ms, latency=2817.547ms, met_slo=False
- req-081: arrival=4082.373ms, dispatch=4141.111ms, tokens=870, slo=1500.000ms, complete=5461.248ms, latency=1378.875ms, met_slo=True
- req-082: arrival=4275.634ms, dispatch=4801.179ms, tokens=66, slo=1500.000ms, complete=6121.317ms, latency=1845.682ms, met_slo=False
- req-083: arrival=4391.296ms, dispatch=4801.179ms, tokens=478, slo=1500.000ms, complete=6121.317ms, latency=1730.021ms, met_slo=False
- req-084: arrival=4450.563ms, dispatch=8761.591ms, tokens=837, slo=1500.000ms, complete=10081.729ms, latency=5631.165ms, met_slo=False
- req-085: arrival=4486.444ms, dispatch=12722.003ms, tokens=1152, slo=1500.000ms, complete=14702.209ms, latency=10215.765ms, met_slo=False
- req-086: arrival=4540.600ms, dispatch=4801.179ms, tokens=42, slo=1500.000ms, complete=6121.317ms, latency=1580.717ms, met_slo=False
- req-087: arrival=4579.695ms, dispatch=4801.179ms, tokens=29, slo=1500.000ms, complete=6121.317ms, latency=1541.622ms, met_slo=False
- req-088: arrival=4589.801ms, dispatch=4801.179ms, tokens=136, slo=1500.000ms, complete=6121.317ms, latency=1531.515ms, met_slo=False
- req-089: arrival=4612.783ms, dispatch=4801.179ms, tokens=229, slo=1500.000ms, complete=6121.317ms, latency=1508.533ms, met_slo=False
- req-090: arrival=4660.245ms, dispatch=4801.179ms, tokens=326, slo=1500.000ms, complete=6121.317ms, latency=1461.071ms, met_slo=True
- req-091: arrival=4705.283ms, dispatch=4801.179ms, tokens=408, slo=1500.000ms, complete=6121.317ms, latency=1416.033ms, met_slo=True
- req-092: arrival=4793.325ms, dispatch=4801.179ms, tokens=667, slo=1500.000ms, complete=6121.317ms, latency=1327.991ms, met_slo=True
- req-093: arrival=4811.629ms, dispatch=5461.248ms, tokens=657, slo=1500.000ms, complete=6781.385ms, latency=1969.757ms, met_slo=False
- req-094: arrival=4849.486ms, dispatch=5461.248ms, tokens=788, slo=1500.000ms, complete=7441.454ms, latency=2591.968ms, met_slo=False
- req-095: arrival=4895.039ms, dispatch=12061.935ms, tokens=1035, slo=1500.000ms, complete=13382.072ms, latency=8487.033ms, met_slo=False
- req-096: arrival=4898.173ms, dispatch=10741.797ms, tokens=930, slo=1500.000ms, complete=12061.935ms, latency=7163.761ms, met_slo=False
- req-097: arrival=4916.941ms, dispatch=10081.729ms, tokens=929, slo=1500.000ms, complete=11401.866ms, latency=6484.925ms, met_slo=False
- req-098: arrival=4923.082ms, dispatch=5461.248ms, tokens=36, slo=1500.000ms, complete=6781.385ms, latency=1858.303ms, met_slo=False
- req-099: arrival=4949.765ms, dispatch=5461.248ms, tokens=103, slo=1500.000ms, complete=6781.385ms, latency=1831.620ms, met_slo=False
- req-100: arrival=4996.258ms, dispatch=5461.248ms, tokens=209, slo=1500.000ms, complete=6781.385ms, latency=1785.127ms, met_slo=False
- req-101: arrival=5026.697ms, dispatch=5461.248ms, tokens=505, slo=1500.000ms, complete=6781.385ms, latency=1754.688ms, met_slo=False
- req-102: arrival=5045.234ms, dispatch=5461.248ms, tokens=166, slo=1500.000ms, complete=6781.385ms, latency=1736.151ms, met_slo=False
- req-103: arrival=5100.608ms, dispatch=5461.248ms, tokens=19, slo=1500.000ms, complete=6781.385ms, latency=1680.778ms, met_slo=False
- req-104: arrival=5117.134ms, dispatch=5461.248ms, tokens=239, slo=1500.000ms, complete=6781.385ms, latency=1664.251ms, met_slo=False
- req-105: arrival=5146.358ms, dispatch=5461.248ms, tokens=357, slo=1500.000ms, complete=6781.385ms, latency=1635.027ms, met_slo=False
- req-106: arrival=5220.461ms, dispatch=5461.248ms, tokens=320, slo=1500.000ms, complete=6781.385ms, latency=1560.925ms, met_slo=False
- req-107: arrival=5243.036ms, dispatch=5461.248ms, tokens=333, slo=1500.000ms, complete=6781.385ms, latency=1538.350ms, met_slo=False
- req-108: arrival=5332.536ms, dispatch=5461.248ms, tokens=342, slo=1500.000ms, complete=6781.385ms, latency=1448.849ms, met_slo=True
- req-109: arrival=5534.198ms, dispatch=6121.317ms, tokens=353, slo=1500.000ms, complete=7441.454ms, latency=1907.256ms, met_slo=False
- req-110: arrival=5573.866ms, dispatch=6121.317ms, tokens=26, slo=1500.000ms, complete=7441.454ms, latency=1867.588ms, met_slo=False
- req-111: arrival=5606.338ms, dispatch=11401.866ms, tokens=950, slo=1500.000ms, complete=12722.003ms, latency=7115.665ms, met_slo=False
- req-112: arrival=5630.147ms, dispatch=6121.317ms, tokens=249, slo=1500.000ms, complete=7441.454ms, latency=1811.307ms, met_slo=False
- req-113: arrival=5635.000ms, dispatch=10741.797ms, tokens=926, slo=1500.000ms, complete=12061.935ms, latency=6426.935ms, met_slo=False
- req-114: arrival=5680.589ms, dispatch=6121.317ms, tokens=495, slo=1500.000ms, complete=7441.454ms, latency=1760.865ms, met_slo=False
- req-115: arrival=5697.671ms, dispatch=6781.385ms, tokens=807, slo=1500.000ms, complete=8101.523ms, latency=2403.852ms, met_slo=False
- req-116: arrival=5731.489ms, dispatch=6121.317ms, tokens=587, slo=1500.000ms, complete=8101.523ms, latency=2370.033ms, met_slo=False
- req-117: arrival=5794.583ms, dispatch=17342.484ms, tokens=1539, slo=1500.000ms, complete=18662.621ms, latency=12868.038ms, met_slo=False
- req-118: arrival=5815.913ms, dispatch=6121.317ms, tokens=428, slo=1500.000ms, complete=7441.454ms, latency=1625.541ms, met_slo=False
- req-119: arrival=5845.078ms, dispatch=6121.317ms, tokens=434, slo=1500.000ms, complete=7441.454ms, latency=1596.376ms, met_slo=False
- req-120: arrival=5910.016ms, dispatch=6121.317ms, tokens=561, slo=1500.000ms, complete=7441.454ms, latency=1531.438ms, met_slo=False
- req-121: arrival=6021.637ms, dispatch=6121.317ms, tokens=565, slo=1500.000ms, complete=7441.454ms, latency=1419.817ms, met_slo=True
- req-122: arrival=6022.783ms, dispatch=6121.317ms, tokens=31, slo=1500.000ms, complete=7441.454ms, latency=1418.671ms, met_slo=True
- req-123: arrival=6036.392ms, dispatch=6121.317ms, tokens=21, slo=1500.000ms, complete=7441.454ms, latency=1405.062ms, met_slo=True
- req-124: arrival=6101.500ms, dispatch=6121.317ms, tokens=578, slo=1500.000ms, complete=7441.454ms, latency=1339.954ms, met_slo=True
- req-125: arrival=6123.895ms, dispatch=7441.454ms, tokens=68, slo=1500.000ms, complete=9421.660ms, latency=3297.765ms, met_slo=False
- req-126: arrival=6180.318ms, dispatch=6781.385ms, tokens=14, slo=1500.000ms, complete=8101.523ms, latency=1921.205ms, met_slo=False
- req-127: arrival=6184.972ms, dispatch=7441.454ms, tokens=839, slo=1500.000ms, complete=8761.591ms, latency=2576.619ms, met_slo=False
- req-128: arrival=6210.577ms, dispatch=6781.385ms, tokens=447, slo=1500.000ms, complete=8761.591ms, latency=2551.015ms, met_slo=False
- req-129: arrival=6220.829ms, dispatch=8761.591ms, tokens=838, slo=1500.000ms, complete=10741.797ms, latency=4520.968ms, met_slo=False
- req-130: arrival=6259.920ms, dispatch=12722.003ms, tokens=1107, slo=1500.000ms, complete=14042.141ms, latency=7782.221ms, met_slo=False
- req-131: arrival=6350.294ms, dispatch=14702.209ms, tokens=1306, slo=1500.000ms, complete=16022.346ms, latency=9672.053ms, met_slo=False
- req-132: arrival=6370.140ms, dispatch=16022.346ms, tokens=1458, slo=1500.000ms, complete=17342.484ms, latency=10972.344ms, met_slo=False
- req-133: arrival=6391.209ms, dispatch=18002.552ms, tokens=1524, slo=1500.000ms, complete=19322.690ms, latency=12931.480ms, met_slo=False
- req-134: arrival=6425.619ms, dispatch=16022.346ms, tokens=1435, slo=1500.000ms, complete=18002.552ms, latency=11576.934ms, met_slo=False
- req-135: arrival=6575.953ms, dispatch=19322.690ms, tokens=1618, slo=1500.000ms, complete=21302.896ms, latency=14726.943ms, met_slo=False
- req-136: arrival=6646.926ms, dispatch=6781.385ms, tokens=1536, slo=1500.000ms, complete=8101.523ms, latency=1454.597ms, met_slo=True
- req-137: arrival=6800.747ms, dispatch=15362.278ms, tokens=1385, slo=1500.000ms, complete=17342.484ms, latency=10541.737ms, met_slo=False
- req-138: arrival=6936.969ms, dispatch=13382.072ms, tokens=1113, slo=1500.000ms, complete=15362.278ms, latency=8425.309ms, met_slo=False
- req-139: arrival=6961.031ms, dispatch=8101.523ms, tokens=109, slo=1500.000ms, complete=9421.660ms, latency=2460.629ms, met_slo=False
- req-140: arrival=6980.517ms, dispatch=7441.454ms, tokens=45, slo=1500.000ms, complete=8761.591ms, latency=1781.075ms, met_slo=False
- req-141: arrival=7039.379ms, dispatch=7441.454ms, tokens=36, slo=1500.000ms, complete=8761.591ms, latency=1722.213ms, met_slo=False
- req-142: arrival=7058.051ms, dispatch=7441.454ms, tokens=380, slo=1500.000ms, complete=8761.591ms, latency=1703.540ms, met_slo=False
- req-143: arrival=7100.921ms, dispatch=7441.454ms, tokens=28, slo=1500.000ms, complete=8761.591ms, latency=1660.670ms, met_slo=False
- req-144: arrival=7169.425ms, dispatch=7441.454ms, tokens=42, slo=1500.000ms, complete=8761.591ms, latency=1592.167ms, met_slo=False
- req-145: arrival=7174.819ms, dispatch=7441.454ms, tokens=246, slo=1500.000ms, complete=8761.591ms, latency=1586.772ms, met_slo=False
- req-146: arrival=7199.356ms, dispatch=8101.523ms, tokens=395, slo=1500.000ms, complete=10081.729ms, latency=2882.373ms, met_slo=False
- req-147: arrival=7250.687ms, dispatch=14042.141ms, tokens=1254, slo=1500.000ms, complete=15362.278ms, latency=8111.590ms, met_slo=False
- req-148: arrival=7342.313ms, dispatch=10081.729ms, tokens=840, slo=1500.000ms, complete=11401.866ms, latency=4059.553ms, met_slo=False
- req-149: arrival=7358.555ms, dispatch=7441.454ms, tokens=1169, slo=1500.000ms, complete=8761.591ms, latency=1403.036ms, met_slo=True
- req-150: arrival=7442.314ms, dispatch=13382.072ms, tokens=1304, slo=1500.000ms, complete=14702.209ms, latency=7259.895ms, met_slo=False
- req-151: arrival=7503.637ms, dispatch=8101.523ms, tokens=19, slo=1500.000ms, complete=9421.660ms, latency=1918.023ms, met_slo=False
- req-152: arrival=7596.791ms, dispatch=8101.523ms, tokens=92, slo=1500.000ms, complete=9421.660ms, latency=1824.868ms, met_slo=False
- req-153: arrival=7672.641ms, dispatch=8101.523ms, tokens=69, slo=1500.000ms, complete=9421.660ms, latency=1749.019ms, met_slo=False
- req-154: arrival=7676.202ms, dispatch=8101.523ms, tokens=10, slo=1500.000ms, complete=9421.660ms, latency=1745.458ms, met_slo=False
- req-155: arrival=7731.598ms, dispatch=8761.591ms, tokens=531, slo=1500.000ms, complete=10081.729ms, latency=2350.130ms, met_slo=False
- req-156: arrival=7838.327ms, dispatch=8101.523ms, tokens=23, slo=1500.000ms, complete=9421.660ms, latency=1583.333ms, met_slo=False
- req-157: arrival=7845.425ms, dispatch=8101.523ms, tokens=49, slo=1500.000ms, complete=9421.660ms, latency=1576.235ms, met_slo=False
- req-158: arrival=7918.482ms, dispatch=8101.523ms, tokens=40, slo=1500.000ms, complete=9421.660ms, latency=1503.178ms, met_slo=False
- req-159: arrival=7966.549ms, dispatch=8101.523ms, tokens=99, slo=1500.000ms, complete=9421.660ms, latency=1455.111ms, met_slo=True
- req-160: arrival=7988.235ms, dispatch=8101.523ms, tokens=134, slo=1500.000ms, complete=9421.660ms, latency=1433.425ms, met_slo=True
- req-161: arrival=8012.436ms, dispatch=8101.523ms, tokens=42, slo=1500.000ms, complete=9421.660ms, latency=1409.224ms, met_slo=True
- req-162: arrival=8045.696ms, dispatch=8101.523ms, tokens=1877, slo=1500.000ms, complete=9421.660ms, latency=1375.963ms, met_slo=True
- req-163: arrival=8240.130ms, dispatch=8761.591ms, tokens=12, slo=1500.000ms, complete=10081.729ms, latency=1841.598ms, met_slo=False
- req-164: arrival=8250.933ms, dispatch=8761.591ms, tokens=302, slo=1500.000ms, complete=10081.729ms, latency=1830.796ms, met_slo=False
- req-165: arrival=8280.883ms, dispatch=8761.591ms, tokens=447, slo=1500.000ms, complete=10081.729ms, latency=1800.845ms, met_slo=False
- req-166: arrival=8287.878ms, dispatch=8761.591ms, tokens=10, slo=1500.000ms, complete=10081.729ms, latency=1793.851ms, met_slo=False
- req-167: arrival=8311.446ms, dispatch=8761.591ms, tokens=26, slo=1500.000ms, complete=10081.729ms, latency=1770.283ms, met_slo=False
- req-168: arrival=8335.917ms, dispatch=8761.591ms, tokens=103, slo=1500.000ms, complete=10081.729ms, latency=1745.812ms, met_slo=False
- req-169: arrival=8352.226ms, dispatch=21302.896ms, tokens=1737, slo=1500.000ms, complete=22623.033ms, latency=14270.808ms, met_slo=False
- req-170: arrival=8357.152ms, dispatch=8761.591ms, tokens=22, slo=1500.000ms, complete=10081.729ms, latency=1724.577ms, met_slo=False
- req-171: arrival=8359.201ms, dispatch=8761.591ms, tokens=111, slo=1500.000ms, complete=10081.729ms, latency=1722.527ms, met_slo=False
- req-172: arrival=8409.905ms, dispatch=8761.591ms, tokens=100, slo=1500.000ms, complete=10081.729ms, latency=1671.823ms, met_slo=False
- req-173: arrival=8471.625ms, dispatch=16682.415ms, tokens=1416, slo=1500.000ms, complete=18662.621ms, latency=10190.996ms, met_slo=False
- req-174: arrival=8508.600ms, dispatch=8761.591ms, tokens=39, slo=1500.000ms, complete=10081.729ms, latency=1573.128ms, met_slo=False
- req-175: arrival=8560.600ms, dispatch=8761.591ms, tokens=22, slo=1500.000ms, complete=10081.729ms, latency=1521.128ms, met_slo=False
- req-176: arrival=8569.113ms, dispatch=8761.591ms, tokens=13, slo=1500.000ms, complete=10081.729ms, latency=1512.616ms, met_slo=False
- req-177: arrival=8741.574ms, dispatch=8761.591ms, tokens=405, slo=1500.000ms, complete=10081.729ms, latency=1340.154ms, met_slo=True
- req-178: arrival=8837.661ms, dispatch=9421.660ms, tokens=38, slo=1500.000ms, complete=10741.797ms, latency=1904.136ms, met_slo=False
- req-179: arrival=8946.906ms, dispatch=9421.660ms, tokens=708, slo=1500.000ms, complete=11401.866ms, latency=2454.960ms, met_slo=False
- req-180: arrival=8953.129ms, dispatch=11401.866ms, tokens=963, slo=1500.000ms, complete=13382.072ms, latency=4428.942ms, met_slo=False
- req-181: arrival=9001.524ms, dispatch=15362.278ms, tokens=1276, slo=1500.000ms, complete=16682.415ms, latency=7680.891ms, met_slo=False
- req-182: arrival=9046.780ms, dispatch=12061.935ms, tokens=1065, slo=1500.000ms, complete=14042.141ms, latency=4995.361ms, met_slo=False
- req-183: arrival=9192.310ms, dispatch=9421.660ms, tokens=271, slo=1500.000ms, complete=10741.797ms, latency=1549.488ms, met_slo=False
- req-184: arrival=9239.416ms, dispatch=14702.209ms, tokens=1314, slo=1500.000ms, complete=16682.415ms, latency=7442.999ms, met_slo=False
- req-185: arrival=9334.402ms, dispatch=9421.660ms, tokens=337, slo=1500.000ms, complete=10741.797ms, latency=1407.396ms, met_slo=True
- req-186: arrival=9378.648ms, dispatch=9421.660ms, tokens=123, slo=1500.000ms, complete=10741.797ms, latency=1363.149ms, met_slo=True
- req-187: arrival=9388.017ms, dispatch=9421.660ms, tokens=387, slo=1500.000ms, complete=10741.797ms, latency=1353.780ms, met_slo=True
- req-188: arrival=9402.267ms, dispatch=9421.660ms, tokens=102, slo=1500.000ms, complete=10741.797ms, latency=1339.530ms, met_slo=True
- req-189: arrival=9414.406ms, dispatch=9421.660ms, tokens=854, slo=1500.000ms, complete=10741.797ms, latency=1327.391ms, met_slo=True
- req-190: arrival=9414.418ms, dispatch=9421.660ms, tokens=522, slo=1500.000ms, complete=10741.797ms, latency=1327.379ms, met_slo=True
- req-191: arrival=9418.227ms, dispatch=9421.660ms, tokens=916, slo=1500.000ms, complete=10741.797ms, latency=1323.571ms, met_slo=True
- req-192: arrival=9568.151ms, dispatch=10081.729ms, tokens=603, slo=1500.000ms, complete=11401.866ms, latency=1833.714ms, met_slo=False
- req-193: arrival=9621.077ms, dispatch=10081.729ms, tokens=628, slo=1500.000ms, complete=12061.935ms, latency=2440.858ms, met_slo=False
- req-194: arrival=9633.748ms, dispatch=10081.729ms, tokens=31, slo=1500.000ms, complete=11401.866ms, latency=1768.118ms, met_slo=False
- req-195: arrival=9637.399ms, dispatch=10081.729ms, tokens=16, slo=1500.000ms, complete=11401.866ms, latency=1764.466ms, met_slo=False
- req-196: arrival=9650.833ms, dispatch=18662.621ms, tokens=1640, slo=1500.000ms, complete=19982.758ms, latency=10331.926ms, met_slo=False
- req-197: arrival=9662.196ms, dispatch=14042.141ms, tokens=1232, slo=1500.000ms, complete=15362.278ms, latency=5700.082ms, met_slo=False
- req-198: arrival=9701.830ms, dispatch=10081.729ms, tokens=489, slo=1500.000ms, complete=11401.866ms, latency=1700.036ms, met_slo=False
- req-199: arrival=9715.619ms, dispatch=10081.729ms, tokens=40, slo=1500.000ms, complete=11401.866ms, latency=1686.247ms, met_slo=False
- req-200: arrival=9875.423ms, dispatch=10081.729ms, tokens=461, slo=1500.000ms, complete=11401.866ms, latency=1526.442ms, met_slo=False
- req-201: arrival=10083.467ms, dispatch=10741.797ms, tokens=362, slo=1500.000ms, complete=12061.935ms, latency=1978.467ms, met_slo=False
- req-202: arrival=10157.687ms, dispatch=10741.797ms, tokens=149, slo=1500.000ms, complete=12061.935ms, latency=1904.248ms, met_slo=False
- req-203: arrival=10188.001ms, dispatch=17342.484ms, tokens=1540, slo=1500.000ms, complete=18662.621ms, latency=8474.620ms, met_slo=False
- req-204: arrival=10259.227ms, dispatch=12061.935ms, tokens=974, slo=1500.000ms, complete=13382.072ms, latency=3122.845ms, met_slo=False
- req-205: arrival=10312.058ms, dispatch=10741.797ms, tokens=541, slo=1500.000ms, complete=12061.935ms, latency=1749.876ms, met_slo=False
- req-206: arrival=10445.352ms, dispatch=11401.866ms, tokens=954, slo=1500.000ms, complete=12722.003ms, latency=2276.651ms, met_slo=False
- req-207: arrival=10466.892ms, dispatch=10741.797ms, tokens=930, slo=1500.000ms, complete=12722.003ms, latency=2255.111ms, met_slo=False
- req-208: arrival=10487.266ms, dispatch=10741.797ms, tokens=34, slo=1500.000ms, complete=12061.935ms, latency=1574.669ms, met_slo=False
- req-209: arrival=10494.583ms, dispatch=13382.072ms, tokens=1169, slo=1500.000ms, complete=14702.209ms, latency=4207.626ms, met_slo=False
- req-210: arrival=10527.573ms, dispatch=16682.415ms, tokens=1512, slo=1500.000ms, complete=18002.552ms, latency=7474.979ms, met_slo=False
- req-211: arrival=10561.405ms, dispatch=21302.896ms, tokens=1994, slo=1500.000ms, complete=22783.792ms, latency=12222.387ms, met_slo=False
- req-212: arrival=10633.089ms, dispatch=10741.797ms, tokens=489, slo=1500.000ms, complete=12061.935ms, latency=1428.846ms, met_slo=True
- req-213: arrival=10755.485ms, dispatch=11401.866ms, tokens=72, slo=1500.000ms, complete=12722.003ms, latency=1966.518ms, met_slo=False
- req-214: arrival=10809.046ms, dispatch=19982.758ms, tokens=1692, slo=1500.000ms, complete=21302.896ms, latency=10493.850ms, met_slo=False
- req-215: arrival=10812.415ms, dispatch=11401.866ms, tokens=849, slo=1500.000ms, complete=12722.003ms, latency=1909.588ms, met_slo=False
- req-216: arrival=10875.016ms, dispatch=11401.866ms, tokens=490, slo=1500.000ms, complete=12722.003ms, latency=1846.987ms, met_slo=False
- req-217: arrival=10883.875ms, dispatch=11401.866ms, tokens=205, slo=1500.000ms, complete=12722.003ms, latency=1838.128ms, met_slo=False
- req-218: arrival=10999.420ms, dispatch=18002.552ms, tokens=1390, slo=1500.000ms, complete=19982.758ms, latency=8983.338ms, met_slo=False

## vLLM Batches

### vLLM Batch 1

- start_ms: 250.506
- end_ms: 365.873
- scheduled_tokens: 472
- chunks: req-001 472t completed=True

### vLLM Batch 2

- start_ms: 365.873
- end_ms: 840.767
- scheduled_tokens: 2864
- chunks: req-002 1087t completed=True; req-003 417t completed=True; req-004 1360t completed=True

### vLLM Batch 3

- start_ms: 840.767
- end_ms: 1500.836
- scheduled_tokens: 4096
- chunks: req-005 185t completed=True; req-006 586t completed=True; req-007 37t completed=True; req-008 54t completed=True; req-009 1528t completed=True; req-010 89t completed=True; req-011 549t completed=True; req-012 172t completed=True; req-013 896t completed=False

### vLLM Batch 4

- start_ms: 1500.836
- end_ms: 2160.905
- scheduled_tokens: 4096
- chunks: req-013 73t completed=True; req-016 253t completed=True; req-017 23t completed=True; req-018 509t completed=True; req-019 387t completed=True; req-020 733t completed=True; req-021 1305t completed=True; req-014 97t completed=True; req-015 716t completed=False

### vLLM Batch 5

- start_ms: 2160.905
- end_ms: 2820.973
- scheduled_tokens: 4096
- chunks: req-015 858t completed=True; req-022 17t completed=True; req-028 92t completed=True; req-027 19t completed=True; req-025 1437t completed=True; req-026 1336t completed=True; req-023 337t completed=False

### vLLM Batch 6

- start_ms: 2820.973
- end_ms: 3481.042
- scheduled_tokens: 4096
- chunks: req-023 264t completed=True; req-043 137t completed=True; req-041 1698t completed=True; req-036 14t completed=True; req-042 1907t completed=True; req-029 76t completed=False

### vLLM Batch 7

- start_ms: 3481.042
- end_ms: 4141.111
- scheduled_tokens: 4096
- chunks: req-029 431t completed=True; req-053 82t completed=True; req-044 20t completed=True; req-056 942t completed=True; req-054 506t completed=True; req-049 18t completed=True; req-050 101t completed=True; req-051 112t completed=True; req-055 530t completed=True; req-030 938t completed=True; req-052 264t completed=True; req-045 127t completed=True; req-039 25t completed=False

### vLLM Batch 8

- start_ms: 4141.111
- end_ms: 4801.179
- scheduled_tokens: 4096
- chunks: req-039 589t completed=True; req-065 1762t completed=True; req-066 1625t completed=True; req-059 24t completed=True; req-067 25t completed=True; req-061 71t completed=False

### vLLM Batch 9

- start_ms: 4801.179
- end_ms: 5461.248
- scheduled_tokens: 4096
- chunks: req-061 32t completed=True; req-079 352t completed=True; req-046 665t completed=True; req-063 200t completed=True; req-078 20t completed=True; req-034 1109t completed=True; req-073 266t completed=True; req-072 383t completed=True; req-069 42t completed=True; req-081 870t completed=True; req-058 122t completed=True; req-076 35t completed=False

### vLLM Batch 10

- start_ms: 5461.248
- end_ms: 6121.317
- scheduled_tokens: 4096
- chunks: req-076 505t completed=True; req-087 29t completed=True; req-086 42t completed=True; req-082 66t completed=True; req-088 136t completed=True; req-089 229t completed=True; req-090 326t completed=True; req-091 408t completed=True; req-083 478t completed=True; req-092 667t completed=True; req-060 698t completed=True; req-080 512t completed=False

### vLLM Batch 11

- start_ms: 6121.317
- end_ms: 6781.385
- scheduled_tokens: 4096
- chunks: req-080 214t completed=True; req-103 19t completed=True; req-098 36t completed=True; req-099 103t completed=True; req-102 166t completed=True; req-100 209t completed=True; req-104 239t completed=True; req-106 320t completed=True; req-107 333t completed=True; req-108 342t completed=True; req-105 357t completed=True; req-101 505t completed=True; req-093 657t completed=True; req-094 596t completed=False

### vLLM Batch 12

- start_ms: 6781.385
- end_ms: 7441.454
- scheduled_tokens: 4096
- chunks: req-094 192t completed=True; req-123 21t completed=True; req-110 26t completed=True; req-122 31t completed=True; req-112 249t completed=True; req-109 353t completed=True; req-118 428t completed=True; req-119 434t completed=True; req-114 495t completed=True; req-120 561t completed=True; req-121 565t completed=True; req-124 578t completed=True; req-116 163t completed=False

### vLLM Batch 13

- start_ms: 7441.454
- end_ms: 8101.523
- scheduled_tokens: 4096
- chunks: req-116 424t completed=True; req-136 1536t completed=True; req-038 1306t completed=True; req-115 807t completed=True; req-126 14t completed=True; req-128 9t completed=False

### vLLM Batch 14

- start_ms: 8101.523
- end_ms: 8761.591
- scheduled_tokens: 4096
- chunks: req-128 438t completed=True; req-144 42t completed=True; req-047 855t completed=True; req-141 36t completed=True; req-140 45t completed=True; req-145 246t completed=True; req-142 380t completed=True; req-143 28t completed=True; req-149 1169t completed=True; req-127 839t completed=True; req-125 18t completed=False

### vLLM Batch 15

- start_ms: 8761.591
- end_ms: 9421.660
- scheduled_tokens: 4096
- chunks: req-125 50t completed=True; req-158 40t completed=True; req-160 134t completed=True; req-153 69t completed=True; req-162 1877t completed=True; req-154 10t completed=True; req-151 19t completed=True; req-161 42t completed=True; req-074 1450t completed=True; req-156 23t completed=True; req-157 49t completed=True; req-152 92t completed=True; req-159 99t completed=True; req-139 109t completed=True; req-146 33t completed=False

### vLLM Batch 16

- start_ms: 9421.660
- end_ms: 10081.729
- scheduled_tokens: 4096
- chunks: req-146 362t completed=True; req-166 10t completed=True; req-163 12t completed=True; req-176 13t completed=True; req-170 22t completed=True; req-175 22t completed=True; req-167 26t completed=True; req-174 39t completed=True; req-172 100t completed=True; req-168 103t completed=True; req-171 111t completed=True; req-164 302t completed=True; req-177 405t completed=True; req-165 447t completed=True; req-155 531t completed=True; req-084 837t completed=True; req-129 754t completed=False

### vLLM Batch 17

- start_ms: 10081.729
- end_ms: 10741.797
- scheduled_tokens: 4096
- chunks: req-129 84t completed=True; req-186 123t completed=True; req-188 102t completed=True; req-183 271t completed=True; req-178 38t completed=True; req-190 522t completed=True; req-185 337t completed=True; req-189 854t completed=True; req-187 387t completed=True; req-191 916t completed=True; req-179 462t completed=False

### vLLM Batch 18

- start_ms: 10741.797
- end_ms: 11401.866
- scheduled_tokens: 4096
- chunks: req-179 246t completed=True; req-194 31t completed=True; req-195 16t completed=True; req-199 40t completed=True; req-200 461t completed=True; req-198 489t completed=True; req-192 603t completed=True; req-148 840t completed=True; req-097 929t completed=True; req-193 441t completed=False

### vLLM Batch 19

- start_ms: 11401.866
- end_ms: 12061.935
- scheduled_tokens: 4096
- chunks: req-193 187t completed=True; req-208 34t completed=True; req-202 149t completed=True; req-201 362t completed=True; req-212 489t completed=True; req-205 541t completed=True; req-113 926t completed=True; req-096 930t completed=True; req-207 478t completed=False

### vLLM Batch 20

- start_ms: 12061.935
- end_ms: 12722.003
- scheduled_tokens: 4096
- chunks: req-207 452t completed=True; req-213 72t completed=True; req-217 205t completed=True; req-216 490t completed=True; req-215 849t completed=True; req-111 950t completed=True; req-206 954t completed=True; req-180 124t completed=False

### vLLM Batch 21

- start_ms: 12722.003
- end_ms: 13382.072
- scheduled_tokens: 4096
- chunks: req-180 839t completed=True; req-204 974t completed=True; req-033 991t completed=True; req-095 1035t completed=True; req-182 257t completed=False

### vLLM Batch 22

- start_ms: 13382.072
- end_ms: 14042.141
- scheduled_tokens: 4096
- chunks: req-182 808t completed=True; req-062 1073t completed=True; req-130 1107t completed=True; req-085 1108t completed=False

### vLLM Batch 23

- start_ms: 14042.141
- end_ms: 14702.209
- scheduled_tokens: 4096
- chunks: req-085 44t completed=True; req-048 1191t completed=True; req-209 1169t completed=True; req-150 1304t completed=True; req-138 388t completed=False

### vLLM Batch 24

- start_ms: 14702.209
- end_ms: 15362.278
- scheduled_tokens: 4096
- chunks: req-138 725t completed=True; req-197 1232t completed=True; req-147 1254t completed=True; req-024 885t completed=False

### vLLM Batch 25

- start_ms: 15362.278
- end_ms: 16022.346
- scheduled_tokens: 4096
- chunks: req-024 442t completed=True; req-064 1282t completed=True; req-131 1306t completed=True; req-184 1066t completed=False

### vLLM Batch 26

- start_ms: 16022.346
- end_ms: 16682.415
- scheduled_tokens: 4096
- chunks: req-184 248t completed=True; req-181 1276t completed=True; req-057 1364t completed=True; req-137 1208t completed=False

### vLLM Batch 27

- start_ms: 16682.415
- end_ms: 17342.484
- scheduled_tokens: 4096
- chunks: req-137 177t completed=True; req-132 1458t completed=True; req-035 1476t completed=True; req-134 985t completed=False

### vLLM Batch 28

- start_ms: 17342.484
- end_ms: 18002.552
- scheduled_tokens: 4096
- chunks: req-134 450t completed=True; req-210 1512t completed=True; req-031 1544t completed=True; req-173 590t completed=False

### vLLM Batch 29

- start_ms: 18002.552
- end_ms: 18662.621
- scheduled_tokens: 4096
- chunks: req-173 826t completed=True; req-117 1539t completed=True; req-203 1540t completed=True; req-075 191t completed=False

### vLLM Batch 30

- start_ms: 18662.621
- end_ms: 19322.690
- scheduled_tokens: 4096
- chunks: req-075 1440t completed=True; req-133 1524t completed=True; req-218 1132t completed=False

### vLLM Batch 31

- start_ms: 19322.690
- end_ms: 19982.758
- scheduled_tokens: 4096
- chunks: req-218 258t completed=True; req-196 1640t completed=True; req-070 1637t completed=True; req-040 561t completed=False

### vLLM Batch 32

- start_ms: 19982.758
- end_ms: 20642.827
- scheduled_tokens: 4096
- chunks: req-040 1399t completed=True; req-068 1639t completed=True; req-135 1058t completed=False

### vLLM Batch 33

- start_ms: 20642.827
- end_ms: 21302.896
- scheduled_tokens: 4096
- chunks: req-135 560t completed=True; req-214 1692t completed=True; req-037 1741t completed=True; req-032 103t completed=False

### vLLM Batch 34

- start_ms: 21302.896
- end_ms: 21962.964
- scheduled_tokens: 4096
- chunks: req-032 1737t completed=True; req-071 1701t completed=True; req-077 658t completed=False

### vLLM Batch 35

- start_ms: 21962.964
- end_ms: 22623.033
- scheduled_tokens: 4096
- chunks: req-077 1139t completed=True; req-169 1737t completed=True; req-211 1220t completed=False

### vLLM Batch 36

- start_ms: 22623.033
- end_ms: 22783.792
- scheduled_tokens: 774
- chunks: req-211 774t completed=True
