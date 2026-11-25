from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_REVIEWS = 50
MAX_REVIEW_LENGTH = 2000


class AnalizIstegi(BaseModel):
    """Ürün yorum analiz isteği şeması."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "urun_adi": "Kablosuz Kulaklık X1",
                "yorumlar": [
                    "Şarjı 2 saatte bitiyor, kutu kapağı da gevşek.",
                    "Ses kalitesi güzel ama sağ kulaklık sık sık kopuyor.",
                ],
                "dil": "tr",
            }
        }
    )

    urun_adi: str = Field(..., min_length=1, max_length=200, description="Analiz edilecek ürün adı")
    yorumlar: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_REVIEWS,
        description=f"En az 1, en fazla {MAX_REVIEWS} adet müşteri yorumu",
    )
    dil: Literal["tr", "en"] = Field(
        default="tr",
        description="Analiz özetleri ve öneriler için çıktı dili",
    )

    @field_validator("urun_adi")
    @classmethod
    def validate_urun_adi(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Ürün adı boş veya yalnızca boşluk karakterlerinden oluşamaz.")
        return cleaned

    @field_validator("yorumlar")
    @classmethod
    def validate_yorumlar(cls, values: list[str]) -> list[str]:
        cleaned_reviews: list[str] = []
        for idx, review in enumerate(values, start=1):
            cleaned = review.strip()
            if not cleaned:
                raise ValueError(f"{idx}. yorum boş veya yalnızca boşluk içeriyor.")
            if len(cleaned) > MAX_REVIEW_LENGTH:
                raise ValueError(
                    f"{idx}. yorum {MAX_REVIEW_LENGTH} karakter sınırını aşıyor."
                )
            cleaned_reviews.append(cleaned)
        return cleaned_reviews


class PainPoint(BaseModel):
    """Tespit edilen müşteri şikayet başlığı."""

    issue: str = Field(..., min_length=1, description="Kısa sorun başlığı")
    frequency: Literal["High", "Medium", "Low"] = Field(
        ..., description="Sorunun görülme sıklığı"
    )
    summary: str = Field(..., min_length=1, description="Sorunun kısa açıklaması")


class AnalizYaniti(BaseModel):
    """Yapılandırılmış kusur analizi yanıt şeması."""

    top_pain_points: list[PainPoint] = Field(
        default_factory=list,
        description="Öne çıkan şikayet noktaları",
    )
    improvement_suggestions: list[str] = Field(
        default_factory=list,
        description="İyileştirme önerileri",
    )
    risk_score_out_of_100: int = Field(
        ...,
        ge=0,
        le=100,
        description="0-100 arası ürün risk puanı",
    )


class SaglikYaniti(BaseModel):
    """Servis sağlık kontrolü yanıtı."""

    durum: str
    model: str
    api_anahtari_tanimli: bool
