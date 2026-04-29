# coding: utf-8
keywords = ["ANR", "Fatal", "CarPlay", "iAP2", "Watchdog", "backtrace", "Deadlock"]
input_file = "logcat_2026_04_17_16_07_08_561-724.txt"  # 换成你的文件名
output_file = "filtered_log.txt"

with open(input_file, "r", encoding="utf-8", errors="ignore") as f, open(
    output_file, "w", encoding="utf-8"
) as out:
    for line in f:
        if any(key.lower() in line.lower() for key in keywords):
            out.write(line)

print("过滤完成，请查看 filtered_log.txt")
