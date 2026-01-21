from datetime import datetime

now = datetime.now()

# Windows 专用格式
formatted_date = now.strftime("%#m月%#d日")

# Windows 环境
# formatted_date = now.strftime("%#m月%#d日")

print(formatted_date) # 输出: 1月19日 (去掉了 01 和 09 中的 0)