"""
专业的 JWT Token 认证系统
使用最新技术栈:
- FastAPI 0.109+ (现代异步Web框架)
- PyJWT 2.8+ (JWT标准实现)
- python-jose[cryptography] (加密支持)
- passlib[bcrypt] (密码哈希)
- pydantic 2.0+ (数据验证)

安装依赖:
pip install fastapi uvicorn python-jose[cryptography] passlib[bcrypt] python-multipart pydantic pydantic-settings
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated
from fastapi import FastAPI, Depends, HTTPException, status, Security
from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
)
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from pydantic_settings import BaseSettings
import secrets


# ==================== 配置管理 ====================
class Settings(BaseSettings):
    """应用配置 - 生产环境应从环境变量读取"""

    SECRET_KEY: str = secrets.token_urlsafe(32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    model_config = ConfigDict(env_file=".env")


settings = Settings()

# ==================== 密码加密上下文 ====================
pwd_context = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12  # 增加安全性
)

# ==================== 安全方案 ====================
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
security = HTTPBearer()


# ==================== 数据模型 ====================
class User(BaseModel):
    """用户模型"""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = None
    disabled: bool = False
    roles: list[str] = Field(default_factory=list)


class UserInDB(User):
    """数据库用户模型"""

    hashed_password: str


class Token(BaseModel):
    """Token响应模型"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token数据模型"""

    username: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    """用户创建模型"""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None


# ==================== 模拟数据库 ====================
fake_users_db = {
    "admin": {
        "username": "admin",
        "email": "admin@example.com",
        "full_name": "Admin User",
        "hashed_password": pwd_context.hash("admin123"),
        "disabled": False,
        "roles": ["admin", "user"],
    },
    "johndoe": {
        "username": "johndoe",
        "email": "john@example.com",
        "full_name": "John Doe",
        "hashed_password": pwd_context.hash("secret123"),
        "disabled": False,
        "roles": ["user"],
    },
}


# ==================== 工具函数 ====================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def get_user(username: str) -> Optional[UserInDB]:
    """从数据库获取用户"""
    if username in fake_users_db:
        user_dict = fake_users_db[username]
        return UserInDB(**user_dict)
    return None


def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    """认证用户"""
    user = get_user(username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_token(data: dict, expires_delta: timedelta) -> str:
    """创建JWT Token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update(
        {
            "exp": expire,
            "iat": datetime.now(timezone.utc),  # 签发时间
            "jti": secrets.token_urlsafe(16),  # JWT ID，用于追踪
        }
    )
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def create_access_token(username: str, scopes: list[str]) -> str:
    """创建访问令牌"""
    return create_token(
        data={"sub": username, "scopes": scopes, "type": "access"},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(username: str) -> str:
    """创建刷新令牌"""
    return create_token(
        data={"sub": username, "type": "refresh"},
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    """获取当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        username: str = payload.get("sub")
        token_type: str = payload.get("type")

        if username is None or token_type != "access":
            raise credentials_exception

        token_data = TokenData(username=username, scopes=payload.get("scopes", []))
    except JWTError:
        raise credentials_exception

    user = get_user(username=token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """获取当前活跃用户"""
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return current_user


def require_role(required_role: str):
    """角色权限检查装饰器"""

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if required_role not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {required_role} 角色权限",
            )
        return current_user

    return role_checker


# ==================== FastAPI 应用 ====================
app = FastAPI(
    title="专业JWT认证API",
    description="使用最新技术栈的JWT Token认证系统",
    version="1.0.0",
)


@app.post("/token", response_model=Token)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """用户登录 - 获取JWT Token"""
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(username=user.username, scopes=user.roles)
    refresh_token = create_refresh_token(username=user.username)

    return Token(access_token=access_token, refresh_token=refresh_token)


@app.post("/refresh", response_model=Token)
async def refresh_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
):
    """刷新访问令牌"""
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的刷新令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        username: str = payload.get("sub")
        token_type: str = payload.get("type")

        if username is None or token_type != "refresh":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = get_user(username)
    if user is None:
        raise credentials_exception

    access_token = create_access_token(username=user.username, scopes=user.roles)
    refresh_token = create_refresh_token(username=user.username)

    return Token(access_token=access_token, refresh_token=refresh_token)


@app.post("/register", response_model=User)
async def register(user_data: UserCreate):
    """用户注册"""
    if user_data.username in fake_users_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在"
        )

    hashed_password = get_password_hash(user_data.password)
    new_user = {
        "username": user_data.username,
        "email": user_data.email,
        "full_name": user_data.full_name,
        "hashed_password": hashed_password,
        "disabled": False,
        "roles": ["user"],
    }
    fake_users_db[user_data.username] = new_user

    return User(**new_user)


@app.get("/users/me", response_model=User)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """获取当前用户信息"""
    return current_user


@app.get("/admin/users")
async def admin_get_users(
    current_user: Annotated[User, Depends(require_role("admin"))],
):
    """管理员端点 - 获取所有用户"""
    return {
        "message": "管理员权限验证成功",
        "users": [
            {k: v for k, v in user.items() if k != "hashed_password"}
            for user in fake_users_db.values()
        ],
    }


@app.get("/protected")
async def protected_route(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """受保护的路由"""
    return {
        "message": f"你好, {current_user.full_name or current_user.username}!",
        "roles": current_user.roles,
    }


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "JWT认证API",
        "endpoints": {
            "登录": "POST /token",
            "注册": "POST /register",
            "刷新令牌": "POST /refresh",
            "当前用户": "GET /users/me",
            "受保护路由": "GET /protected",
            "管理员路由": "GET /admin/users",
            "API文档": "/docs",
        },
    }


# ==================== 启动说明 ====================
"""
运行方式:
uvicorn main:app --reload

测试流程:
1. 注册用户: POST /register
   {
     "username": "testuser",
     "email": "test@example.com",
     "password": "password123",
     "full_name": "Test User"
   }

2. 登录获取Token: POST /token
   username=admin&password=admin123

3. 使用Token访问: GET /users/me
   Header: Authorization: Bearer <access_token>

4. 刷新Token: POST /refresh
   Header: Authorization: Bearer <refresh_token>

5. 访问管理员路由: GET /admin/users (需要admin角色)
   Header: Authorization: Bearer <access_token>
"""
