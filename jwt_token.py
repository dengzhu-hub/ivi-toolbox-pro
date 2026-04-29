import jwt
import datetime

# 1. 设置密钥 (Secret Key)
# 在实际生产中，这个密钥应该存放在环境变量或安全配置文件中，绝对不能泄露
SECRET_KEY = "127189247189jadhajksd"

# 2. 构建 Payload (载荷)
# 这里包含用户信息和过期时间
payload = {
    "user_id": 12345,
    "username": "Gemini_User",
    "role": "admin",
    # exp (Expiration Time): 设置过期时间，这是 JWT 的标准字段
    "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    # iat (Issued At): 签发时间
    "iat": datetime.datetime.now(datetime.timezone.utc),
}

# 3. 生成 Token (Encoding)
# 使用 HS256 算法进行签名
token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
print(f"生成的 Token:\n{token}\n")

# ---------------------------------------------------------

# 4. 验证并解码 Token (Decoding)
try:
    # 解码时需要提供相同的密钥和算法
    decoded_payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    print("解码后的数据:")
    print(decoded_payload)
except jwt.ExpiredSignatureError:
    print("错误: Token 已过期！")
except jwt.InvalidTokenError:
    print("错误: Token 无效（可能被篡改或密钥错误）")
