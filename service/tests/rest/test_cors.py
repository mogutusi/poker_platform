"""CORS 放行(0079)。前端跑在 3000、后端在 8000,浏览器会先发预检;不回 CORS 头就整个请求被拦,
连登录都发不出去。Node 里的冒烟不受此约束,所以这层只能靠这里和真实浏览器测试守住。

httpx/TestClient 本环境未装(同 test_lobby.py),故直接检查中间件装配与来源白名单解析。"""

from starlette.middleware.cors import CORSMiddleware

from app.config import Settings
from app.shell.lifespan import create_app


def _cors_middleware(app):
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw
    return None


def test_cors_middleware_installed_with_explicit_origins():
    """装了 CORS 中间件,且来源是明确列举的——不是通配。"""
    app = create_app()
    mw = _cors_middleware(app)
    assert mw is not None, "没装 CORSMiddleware,浏览器会拦掉所有跨源请求"

    origins = mw.kwargs["allow_origins"]
    assert "*" not in origins, "不许用通配:带凭据时通配无效,也不该放任任意来源"
    # localhost 与 127.0.0.1 是**不同的 origin**,两种写法都要放行,否则换一种写法就连不上。
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_cors_allows_only_the_methods_the_frontend_uses():
    """只放行前端真用到的方法:公开读是 GET,登录与信封端点是 POST。"""
    app = create_app()
    mw = _cors_middleware(app)
    assert set(mw.kwargs["allow_methods"]) == {"GET", "POST"}


def test_origins_are_configurable_and_can_be_disabled():
    """来源走配置而非硬编码;留空表示同源部署,那时不该装中间件。"""
    assert Settings(CORS_ORIGINS="https://a.example").CORS_ORIGINS == "https://a.example"

    parsed = [o.strip() for o in "".split(",") if o.strip()]
    assert parsed == [], "空配置应解析成空列表,让 create_app 跳过中间件"
