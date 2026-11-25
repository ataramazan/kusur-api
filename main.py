from contextlib import asynccontextmanager
import logging
import time

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from google import genai

from analyzer import (
    AnalyzerConfigError,
    AnalyzerInvalidResponseError,
    AnalyzerRateLimitError,
    AnalyzerTimeoutError,
    AnalyzerUnavailableError,
    AnalyzerUpstreamError,
    analyze_reviews,
    create_genai_client,
)
from config import Settings, get_settings
from schemas import AnalizIstegi, AnalizYaniti, SaglikYaniti
from security import enforce_rate_limit


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


logger = logging.getLogger(__name__)
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)

    if settings.environment == "development" and not (
        settings.api_access_keys or settings.rapidapi_proxy_secret
    ):
        logger.warning(
            "Geliştirme modu: API_ACCESS_KEYS / RAPIDAPI_PROXY_SECRET tanımlı değil; /analiz-et herkese açık."
        )

    client: genai.Client | None = None
    if settings.gemini_api_key:
        client = create_genai_client(settings)
        logger.info(
            "Gemini istemcisi başlatıldı (model=%s, env=%s).",
            settings.gemini_model,
            settings.environment,
        )
    else:
        logger.warning("GEMINI_API_KEY tanımlı değil; /analiz-et istekleri 503 dönecektir.")

    app.state.genai_client = client
    try:
        yield
    finally:
        app.state.genai_client = None


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Product Pain-Point Analyzer API",
        version="1.1.0",
        description="Müşteri yorumlarından ürün kusurlarını, iyileştirme önerilerini ve risk puanını çıkaran ticari API.",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    return application


app = create_app()


def get_genai_client(settings: Settings = Depends(get_settings)) -> genai.Client | None:
    """Uygulama düzeyindeki paylaşılan Gemini istemcisini sağlar."""
    client: genai.Client | None = getattr(app.state, "genai_client", None)
    if client is not None:
        return client

    if not settings.gemini_api_key:
        return None

    client = create_genai_client(settings)
    app.state.genai_client = client
    return client


@app.get("/", response_model=SaglikYaniti)
def ana_sayfa(settings: Settings = Depends(get_settings)) -> SaglikYaniti:
    return SaglikYaniti(
        durum="API aktif ve calisiyor!",
        model=settings.gemini_model,
        api_anahtari_tanimli=bool(settings.gemini_api_key),
    )


@app.get("/health", response_model=SaglikYaniti)
def saglik_kontrolu(settings: Settings = Depends(get_settings)) -> SaglikYaniti:
    return SaglikYaniti(
        durum="ok",
        model=settings.gemini_model,
        api_anahtari_tanimli=bool(settings.gemini_api_key),
    )


@app.post(
    "/analiz-et",
    response_model=AnalizYaniti,
    responses={
        401: {"description": "Geçersiz veya eksik istemci API anahtarı"},
        422: {"description": "Geçersiz istek gövdesi veya doğrulama hatası"},
        429: {"description": "Hız sınırı veya yukarı akış kota limiti aşıldı"},
        502: {"description": "Harici yapay zeka servisi geçersiz yanıt verdi veya erişilemedi"},
        503: {"description": "Servis yapılandırması eksik veya harici servis geçici olarak kullanılamıyor"},
        504: {"description": "Analiz servisi zaman aşımına uğradı"},
    },
)
async def kusur_analizi_yap(
    istek: AnalizIstegi,
    response: Response,
    auth_info: tuple[str, int] = Depends(enforce_rate_limit),
    client: genai.Client | None = Depends(get_genai_client),
    settings: Settings = Depends(get_settings),
) -> AnalizYaniti:
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sunucu yapılandırması eksik: GEMINI_API_KEY tanımlı değil.",
        )

    if not settings.gemini_model:
        settings = settings.model_copy(update={"gemini_model": DEFAULT_GEMINI_MODEL})

    _client_id, remaining_quota = auth_info
    started_at = time.perf_counter()

    try:
        result = await analyze_reviews(client=client, istek=istek, settings=settings)
    except AnalyzerConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AnalyzerRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except AnalyzerUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except AnalyzerTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except (AnalyzerInvalidResponseError, AnalyzerUpstreamError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    response.headers["X-Model-Used"] = result.model_used
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
    if settings.rate_limit_per_minute > 0:
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining_quota)

    return result.data
