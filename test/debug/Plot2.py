import matplotlib.pyplot as plt

def parse_input(input_str):
    lines = input_str.strip().split('\n')
    groups = []
    current_group = None
    current_data = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            group_name = float(line)
            if current_group is not None and current_data:
                groups.append({'group_name': current_group, 'data': current_data})
                current_data = {}
            current_group = group_name
        except ValueError:
            if current_group is None:
                continue
            parts = line.split(':', 1)
            if len(parts) != 2:
                continue
            key, value = parts[0].strip(), parts[1].strip()
            try:
                value = float(value)
                current_data[key] = value
            except ValueError:
                continue
    if current_group is not None and current_data:
        groups.append({'group_name': current_group, 'data': current_data})
    return groups

def compute_averages(groups):
    time_labels = ['Prefill generation time', 'KV Cache transmit time', 'Decode generation time']
    throughput_labels = ['Input throughput', 'Output throughput']

    all_time_data = {label: [] for label in time_labels}
    all_throughput_data = {label: [] for label in throughput_labels}

    for group in groups:
        data = group['data']
        for label in time_labels:
            if label in data:
                all_time_data[label].append(data[label])
        for label in throughput_labels:
            if label in data:
                all_throughput_data[label].append(data[label])

    average_time_data = {label: sum(vals)/len(vals) if vals else 0 for label, vals in all_time_data.items()}
    average_throughput_data = {label: sum(vals)/len(vals) if vals else 0 for label, vals in all_throughput_data.items()}

    return average_time_data, average_throughput_data

def plot_time_bars(average_time_data):
    labels = ['Prefill', 'KV Cache', 'Decode']
    values = [
        average_time_data.get('Prefill generation time', 0),
        average_time_data.get('KV Cache transmit time', 0),
        average_time_data.get('Decode generation time', 0)
    ]
    total_time = sum(values)

    x = range(len(labels) + 1)
    bar_width = 0.4

    plt.figure(figsize=(10, 5))

    # Plot individual bars
    for i, (label, val) in enumerate(zip(labels, values)):
        plt.bar(i, val, bar_width, color=['skyblue', 'lightgreen', 'salmon'][i])

    # Plot stacked bar for total
    plt.bar(3, values[0], bar_width, color='skyblue', label='Prefill')
    plt.bar(3, values[1], bar_width, bottom=values[0], color='lightgreen', label='KV Cache')
    plt.bar(3, values[2], bar_width, bottom=values[0] + values[1], color='salmon', label='Decode')

    # Set x-axis labels
    plt.xticks([i for i in range(len(labels)+1)], [label for label in labels] + ['Total'])
    plt.ylabel('Time (s)')
    plt.title('Time Breakdown and Total')
    plt.legend()
    plt.show()

def plot_time_pie(average_time_data):
    labels = ['Prefill', 'KV Cache', 'Decode']
    values = [
        average_time_data.get('Prefill generation time', 0),
        average_time_data.get('KV Cache transmit time', 0),
        average_time_data.get('Decode generation time', 0)
    ]

    if sum(values) == 0:
        print("No time data to plot pie chart.")
        return

    plt.figure(figsize=(6, 6))
    plt.pie(values, labels=labels, autopct='%1.1f%%', colors=['skyblue', 'lightgreen', 'salmon'])
    plt.title('Time Proportions')
    plt.show()

def plot_throughput_bars(average_throughput_data):
    input_label = 'Input'
    output_label = 'Output'
    input_val = average_throughput_data.get('Input throughput', 0)
    output_val = average_throughput_data.get('Output throughput', 0)
    total_val = input_val + output_val

    labels = [input_label, output_label]
    values = [input_val, output_val]
    x = range(len(labels) + 1)
    bar_width = 0.4

    plt.figure(figsize=(8, 4))

    # Plot individual bars
    for i, (label, val) in enumerate(zip(labels, values)):
        plt.bar(i, val, bar_width, label=label, color=['lightblue', 'lightcoral'][i])

    # Plot stacked bar for total
    plt.bar(2, input_val, bar_width, color='lightblue', label='Input')
    plt.bar(2, output_val, bar_width, bottom=input_val, color='lightcoral', label='Output')

    # Set x-axis labels
    plt.xticks([i for i in range(len(labels)+1)], [label for label in labels] + ['Total'])
    plt.ylabel('Throughput')
    plt.title('Input/Output Throughput and Total')
    plt.legend()
    plt.show()

def main():
    input_str = """8
Prefill generation time: 0.19703245162963867
KV Cache transmit time: 0.12160444259643555
Decode generation time:  1.798290729522705
Input throughput: 10394.226854820949
Output throughput: 1138.8592324799292
Prefill GPU util: 0.00
Prefill GPU mem: 19065.56
Decode GPU util: 53.58
Decode GPU mem: 18992.09
"""

    groups = parse_input(input_str)
    average_time_data, average_throughput_data = compute_averages(groups)

    plot_time_bars(average_time_data)
    plot_time_pie(average_time_data)
    plot_throughput_bars(average_throughput_data)

if __name__ == '__main__':
    main()