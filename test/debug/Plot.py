import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

# 原始字符串数据
data_str = """
1
Prefill generation time: 0.05215144157409668
KV Cache transmit time: 0.026772737503051758
Decode generation time:  1.5175659656524658
Input throughput: 4908.780894124961
Output throughput: 168.69118430046944
1
Prefill generation time: 0.05977439880371094
KV Cache transmit time: 0.039647817611694336
Decode generation time:  1.5068652629852295
Input throughput: 4282.769967133603
Output throughput: 169.88911104954533
1
Prefill generation time: 0.05110049247741699
KV Cache transmit time: 0.031954050064086914
Decode generation time:  1.4996602535247803
Input throughput: 5009.7364543626445
Output throughput: 170.70533102301084
1
Prefill generation time: 0.0631558895111084
KV Cache transmit time: 0.039154052734375
Decode generation time:  1.5014960765838623
Input throughput: 4053.462028350856
Output throughput: 170.49661600344632
1
Prefill generation time: 0.06177020072937012
KV Cache transmit time: 0.03847670555114746
Decode generation time:  1.509465217590332
Input throughput: 4144.393202178452
Output throughput: 169.5964882242674
2
Prefill generation time: 0.07027912139892578
KV Cache transmit time: 0.046853065490722656
Decode generation time:  1.6204142570495605
Input throughput: 7285.236209680703
Output throughput: 315.96858505321114
2
Prefill generation time: 0.0716862678527832
KV Cache transmit time: 0.055214643478393555
Decode generation time:  1.6609015464782715
Input throughput: 7142.23261073455
Output throughput: 308.2663154150409
2
Prefill generation time: 0.0809333324432373
KV Cache transmit time: 0.057955265045166016
Decode generation time:  1.6579678058624268
Input throughput: 6326.194468256844
Output throughput: 308.8117864470067
2
Prefill generation time: 0.07053971290588379
KV Cache transmit time: 0.05011391639709473
Decode generation time:  1.6355228424072266
Input throughput: 7258.322707991821
Output throughput: 313.04973964559144
2
Prefill generation time: 0.0826568603515625
KV Cache transmit time: 0.06711745262145996
Decode generation time:  1.6182756423950195
Input throughput: 6194.28318257338
Output throughput: 316.38614991587525
4
Prefill generation time: 0.11301541328430176
KV Cache transmit time: 0.08617305755615234
Decode generation time:  1.6924896240234375
Input throughput: 9060.711014912842
Output throughput: 605.025865721833
4
Prefill generation time: 0.11484026908874512
KV Cache transmit time: 0.07742619514465332
Decode generation time:  1.6983273029327393
Input throughput: 8916.732850988737
Output throughput: 602.9462037333534
4
Prefill generation time: 0.11435365676879883
KV Cache transmit time: 0.08773374557495117
Decode generation time:  1.7057697772979736
Input throughput: 8954.67647414487
Output throughput: 600.3154784592726
4
Prefill generation time: 0.1175994873046875
KV Cache transmit time: 0.08948898315429688
Decode generation time:  1.6970975399017334
Input throughput: 8707.520954975997
Output throughput: 603.383114950065
4
Prefill generation time: 0.12552237510681152
KV Cache transmit time: 0.0990915298461914
Decode generation time:  1.685817003250122
Input throughput: 8157.9080950997095
Output throughput: 607.4206144710896
8
Prefill generation time: 0.20586013793945312
KV Cache transmit time: 0.18147015571594238
Decode generation time:  1.787966251373291
Input throughput: 9948.502029092931
Output throughput: 1145.4354904221395
8
Prefill generation time: 0.20235323905944824
KV Cache transmit time: 0.16603827476501465
Decode generation time:  1.7840471267700195
Input throughput: 10120.915333598043
Output throughput: 1147.9517380843306
8
Prefill generation time: 0.20396065711975098
KV Cache transmit time: 0.1738595962524414
Decode generation time:  1.803410530090332
Input throughput: 10041.152195335213
Output throughput: 1135.6260628562575
8
Prefill generation time: 0.21104145050048828
KV Cache transmit time: 0.17076635360717773
Decode generation time:  1.7908968925476074
Input throughput: 9704.254757267514
Output throughput: 1143.561088593244
8
Prefill generation time: 0.2054157257080078
KV Cache transmit time: 0.17417240142822266
Decode generation time:  1.7862608432769775
Input throughput: 9970.025386036752
Output throughput: 1146.5290792820886
16
Prefill generation time: 0.39072442054748535
KV Cache transmit time: 0.324751615524292
Decode generation time:  1.9640896320343018
Input throughput: 10483.091879081068
Output throughput: 2085.444540409073
16
Prefill generation time: 0.42010021209716797
KV Cache transmit time: 0.33535218238830566
Decode generation time:  1.9790067672729492
Input throughput: 9750.054587100773
Output throughput: 2069.7251104624797
16
Prefill generation time: 0.3957533836364746
KV Cache transmit time: 0.3205099105834961
Decode generation time:  1.9842987060546875
Input throughput: 10349.879923610317
Output throughput: 2064.2053474619934
16
Prefill generation time: 0.4028937816619873
KV Cache transmit time: 0.2990458011627197
Decode generation time:  1.990109920501709
Input throughput: 10166.451274337089
Output throughput: 2058.177770887848
16
Prefill generation time: 0.3967554569244385
KV Cache transmit time: 0.35266566276550293
Decode generation time:  1.9639897346496582
Input throughput: 10323.739544129516
Output throughput: 2085.550615533464
32
Prefill generation time: 0.674422025680542
KV Cache transmit time: 0.7165367603302002
Decode generation time:  2.3743410110473633
Input throughput: 12146.69700583053
Output throughput: 3450.2204872358943
32
Prefill generation time: 0.8272304534912109
KV Cache transmit time: 0.65970778465271
Decode generation time:  2.3629066944122314
Input throughput: 9902.923623552306
Output throughput: 3466.9164124729623
32
Prefill generation time: 0.769338846206665
KV Cache transmit time: 0.5476157665252686
Decode generation time:  2.3417246341705322
Input throughput: 10648.103940665189
Output throughput: 3498.276390170746
32
Prefill generation time: 0.7821228504180908
KV Cache transmit time: 0.621147871017456
Decode generation time:  2.3293962478637695
Input throughput: 10474.057874182927
Output throughput: 3516.791103064872
32
Prefill generation time: 0.7842690944671631
KV Cache transmit time: 0.7404346466064453
Decode generation time:  2.346083879470825
Input throughput: 10445.39439051298
Output throughput: 3491.7762624274797
64
Prefill generation time: 1.5175106525421143
KV Cache transmit time: 1.4196293354034424
Decode generation time:  3.1401784420013428
Input throughput: 10796.629316936744
Output throughput: 5217.537889202857
64
Prefill generation time: 1.5228629112243652
KV Cache transmit time: 1.160940408706665
Decode generation time:  3.2337217330932617
Input throughput: 10758.68345025715
Output throughput: 5066.607875479643
64
Prefill generation time: 1.3794188499450684
KV Cache transmit time: 1.3409228324890137
Decode generation time:  3.1061456203460693
Input throughput: 11877.465644791246
Output throughput: 5274.704409439306
64
Prefill generation time: 1.4178054332733154
KV Cache transmit time: 1.3615193367004395
Decode generation time:  3.145275354385376
Input throughput: 11555.887440897963
Output throughput: 5209.082879550184
64
Prefill generation time: 1.5776402950286865
KV Cache transmit time: 1.2956123352050781
Decode generation time:  3.1240358352661133
Input throughput: 10385.13028072859
Output throughput: 5244.49809923655
128
Prefill generation time: 3.060276746749878
KV Cache transmit time: 2.5916755199432373
Decode generation time:  4.9266204833984375
Input throughput: 10707.528341938609
Output throughput: 6651.212552381601
128
Prefill generation time: 2.9562759399414062
KV Cache transmit time: 2.6775100231170654
Decode generation time:  5.045902729034424
Input throughput: 11084.215636734325
Output throughput: 6493.981703501929
128
Prefill generation time: 2.7907614707946777
KV Cache transmit time: 2.970285415649414
Decode generation time:  4.8807337284088135
Input throughput: 11741.598249408687
Output throughput: 6713.74465877343
128
Prefill generation time: 2.7910284996032715
KV Cache transmit time: 2.7472143173217773
Decode generation time:  4.938288688659668
Input throughput: 11740.474883956857
Output throughput: 6635.497044805975
128
Prefill generation time: 3.0462889671325684
KV Cache transmit time: 3.032454490661621
Decode generation time:  4.905128240585327
Input throughput: 10756.694572821201
Output throughput: 6680.355414334653
256
Prefill generation time: 5.268307447433472
KV Cache transmit time: 5.243957281112671
Decode generation time:  9.518332242965698
Input throughput: 12439.668841256933
Output throughput: 6885.2398011671485
256
Prefill generation time: 6.016516208648682
KV Cache transmit time: 4.677114486694336
Decode generation time:  9.599726915359497
Input throughput: 10892.682364221451
Output throughput: 6826.86086571305
256
Prefill generation time: 6.08547568321228
KV Cache transmit time: 5.444677114486694
Decode generation time:  9.742990016937256
Input throughput: 10769.248520833158
Output throughput: 6726.477178573717
256
Prefill generation time: 5.385251522064209
KV Cache transmit time: 4.149089813232422
Decode generation time:  9.542236804962158
Input throughput: 12169.533722146285
Output throughput: 6867.99136717294
256
Prefill generation time: 6.0901124477386475
KV Cache transmit time: 5.150727033615112
Decode generation time:  9.678331851959229
Input throughput: 10761.04925194518
Output throughput: 6771.414847356495
"""

# 分割字符串为行
lines = [line.strip() for line in data_str.split('\n') if line.strip()]

# 解析数据
data_points = []
i = 0
while i < len(lines):
    line = lines[i]
    try:
        x = int(line)
        if i + 5 >= len(lines):
            break  # 数据不完整，跳过
        prefill_line = lines[i + 1]
        kv_line = lines[i + 2]
        decode_line = lines[i + 3]
        input_line = lines[i + 4]
        output_line = lines[i + 5]

        prefill_time = float(prefill_line.split(': ')[1].strip())
        kv_time = float(kv_line.split(': ')[1].strip())
        decode_time = float(decode_line.split(': ')[1].strip())
        input_throughput = float(input_line.split(': ')[1].strip())
        output_throughput = float(output_line.split(': ')[1].strip())

        data_points.append((x, prefill_time, kv_time, decode_time, input_throughput, output_throughput))
        i += 6
    except (ValueError, IndexError):
        i += 1

# 分组数据
groups = defaultdict(list)
for dp in data_points:
    x = dp[0]
    prefill, kv, decode, input_t, output_t = dp[1], dp[2], dp[3], dp[4], dp[5]
    groups[x].append((prefill, kv, decode, input_t, output_t))

# 提取并排序 x 值
x_list = sorted(groups.keys())

# 计算平均值与标准差
prefill_avg = []
prefill_std = []
kv_avg = []
kv_std = []
decode_avg = []
decode_std = []
total_avg = []
total_std = []
input_avg = []
input_std = []
output_avg = []
output_std = []

for x in x_list:
    group = groups[x]
    prefill_vals = [g[0] for g in group]
    kv_vals = [g[1] for g in group]
    decode_vals = [g[2] for g in group]
    input_vals = [g[3] for g in group]
    output_vals = [g[4] for g in group]

    # 计算总时间
    total_vals = [p + k + d for p, k, d in zip(prefill_vals, kv_vals, decode_vals)]

    prefill_avg.append(np.mean(prefill_vals))
    prefill_std.append(np.std(prefill_vals))

    kv_avg.append(np.mean(kv_vals))
    kv_std.append(np.std(kv_vals))

    decode_avg.append(np.mean(decode_vals))
    decode_std.append(np.std(decode_vals))

    total_avg.append(np.mean(total_vals))
    total_std.append(np.std(total_vals))

    input_avg.append(np.mean(input_vals))
    input_std.append(np.std(input_vals))

    output_avg.append(np.mean(output_vals))
    output_std.append(np.std(output_vals))

# 创建绘图窗口
fig, axs = plt.subplots(4, 1, figsize=(10, 16))

# 图1：四个时间数据（新增总时间）
axs[0].errorbar(x_list, prefill_avg, yerr=prefill_std, fmt='-o', label='Prefill', capsize=5)
axs[0].errorbar(x_list, kv_avg, yerr=kv_std, fmt='-s', label='KV Cache', capsize=5)
axs[0].errorbar(x_list, decode_avg, yerr=decode_std, fmt='-*', label='Decode', capsize=5)
axs[0].errorbar(x_list, total_avg, yerr=total_std, fmt='-x', color='tab:purple', label='Total', capsize=5)
axs[0].set_xlabel('Batch')
axs[0].set_ylabel('Time (seconds)')
axs[0].set_title('Generation Times')
axs[0].legend()
axs[0].grid(True)
axs[0].set_xticks(x_list)
axs[0].set_xticklabels(x_list, rotation=45)

# 图2：Input Throughput
axs[1].errorbar(x_list, input_avg, yerr=input_std, fmt='-o', color='tab:orange', label='Input', capsize=5)
axs[1].set_xlabel('Batch')
axs[1].set_ylabel('Throughput (tokens/sec)')
axs[1].set_title('Input Throughput')
axs[1].legend()
axs[1].grid(True)
axs[1].set_xticks(x_list)
axs[1].set_xticklabels(x_list, rotation=45)

# 图3：Output Throughput
axs[2].errorbar(x_list, output_avg, yerr=output_std, fmt='-o', color='tab:green', label='Output', capsize=5)
axs[2].set_xlabel('Batch')
axs[2].set_ylabel('Throughput (tokens/sec)')
axs[2].set_title('Output Throughput')
axs[2].legend()
axs[2].grid(True)
axs[2].set_xticks(x_list)
axs[2].set_xticklabels(x_list, rotation=45)

# 图4：Input & Output 吞吐量合并图
axs[3].errorbar(x_list, input_avg, yerr=input_std, fmt='-o', color='tab:blue', label='Input', capsize=5)
axs[3].errorbar(x_list, output_avg, yerr=output_std, fmt='-s', color='tab:red', label='Output', capsize=5)
axs[3].set_xlabel('Batch')
axs[3].set_ylabel('Throughput (tokens/sec)')
axs[3].set_title('Input & Output Throughput')
axs[3].legend()
axs[3].grid(True)
axs[3].set_xticks(x_list)
axs[3].set_xticklabels(x_list, rotation=45)

# 调整布局并显示
plt.tight_layout()
plt.show()