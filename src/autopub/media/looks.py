"""진행자 외형 정의.

얼굴 / 의상 / 배경 / 구도를 따로 둔다. 한 덩어리 프롬프트로 묶어 두면
"이 얼굴 그대로 옷만 바꿔줘" 같은 요구를 들어줄 수 없기 때문이다.

  얼굴(FACES)     : 누구인가 — 시드와 함께 인물을 결정한다
  의상(OUTFITS)   : 무엇을 입었나
  배경(SCENES)    : 어디인가
  구도(FRAMINGS)  : 어떻게 잡았나

⚠️ 확산 모델은 같은 시드라도 프롬프트가 바뀌면 얼굴이 완전히 같지는 않다.
   인상과 분위기는 유지되지만 픽셀 단위로 동일하진 않다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 어떤 조합에도 항상 붙는 제약. 모델이 선정적이거나 비율이 깨진 쪽으로
# 흘러가지 않게 잡아 준다.
LOOK_BASE = "photorealistic, natural proportions, natural hands, sharp focus"

# ----- 얼굴 (앵커 후보와 1:1 대응) -----
FACES: dict[str, str] = {
    "1": "late 20s, chin-length bob haircut, bright approachable smile, natural makeup",
    "2": "early 30s, long straight black hair, calm composed expression, natural makeup",
    "3": "mid 30s, shoulder-length layered hair, poised confident expression, natural makeup",
    "4": "mid 20s, high ponytail, friendly expression, natural makeup",
    "5": "short cropped bob, composed refined expression, natural makeup",
    "6": "early 30s, soft wavy shoulder-length hair, gentle warm expression, natural makeup",
}

# ----- 의상 -----
OUTFITS: dict[str, str] = {
    "anchor_suit": "a NAVY business blazer over a white collared blouse buttoned to the neck",
    # 레퍼런스 캡처 재현: 검정 레이스 트림 니트 + 베이지 체크 플리츠 스커트.
    # 색을 대문자로 강조하지 않으면 모델이 크림색으로 흘러간다(실측).
    "street_knit": "a BLACK lace-trim knit long-sleeve top and a beige plaid pleated skirt",
    "casual_shirt": "a cream oversized shirt with a simple necklace",
}

# ----- 배경 -----
SCENES: dict[str, str] = {
    "studio": "modern broadcast news studio background, soft blue bokeh, studio lighting",
    # 레퍼런스 캡처 재현: 햇빛 드는 거리, 뒤로 관목과 나무
    "street_bench": "sunny Korean street with green trees behind, natural daylight, "
                    "blurred background",
    "cafe": "bright cafe by a window, warm natural light, blurred interior",
}

# ----- 구도 -----
FRAMINGS: dict[str, str] = {
    "head_shoulders": "head and shoulders portrait, centered, facing camera",
    # 세로 영상에서 하단 40%는 자막·카드가 덮으므로 상반신이 중요하다
    "seated_upper": "sitting on a stone ledge outdoors, waist up, centered, "
                    "looking slightly off camera as if speaking",
    "seated_wide": "sitting on a stone ledge outdoors, full figure, centered, "
                   "looking slightly off camera as if speaking",
}


@dataclass
class PresenterLook:
    """한 사람의 외형 한 벌."""

    face: str = "4"
    outfit: str = "anchor_suit"
    scene: str = "studio"
    framing: str = "head_shoulders"
    seed: int = 3011
    extra: str = ""

    @property
    def label(self) -> str:
        return f"얼굴{self.face} · {self.outfit} · {self.scene} · seed {self.seed}"

    def prompt(self) -> str:
        """프롬프트를 조립한다.

        순서가 중요하다. 의상을 뒤쪽에 두거나 전체가 길어지면 모델이 의상을
        무시하고 기본값(크림색 니트)으로 흘러간다 — 실제로 겪은 문제다.
        인물 → 의상 → 구도 → 배경 순으로 앞쪽에 몰아넣고 전체를 짧게 유지한다.
        """
        parts = [
            f"young Korean woman, {FACES.get(self.face, FACES['4'])}",
            f"wearing {OUTFITS.get(self.outfit, OUTFITS['anchor_suit'])}",
            FRAMINGS.get(self.framing, FRAMINGS["head_shoulders"]),
            SCENES.get(self.scene, SCENES["studio"]),
            LOOK_BASE,
        ]
        if self.extra:
            parts.append(self.extra)
        return ", ".join(part for part in parts if part)


# 바로 쓸 수 있는 조합
PRESETS: dict[str, PresenterLook] = {
    # 뉴스 스튜디오 앵커
    "anchor": PresenterLook(
        face="4", outfit="anchor_suit", scene="studio",
        framing="head_shoulders", seed=3011,
    ),
    # 레퍼런스 캡처 재현 — 야외 벤치, 캐주얼 니트
    "street": PresenterLook(
        face="4", outfit="street_knit", scene="street_bench",
        framing="seated_upper", seed=3011,
    ),
}


def resolve(name: str = "street", **overrides) -> PresenterLook:
    """프리셋을 가져와 일부만 바꾼다."""
    base = PRESETS.get(name, PRESETS["street"])
    return PresenterLook(
        face=overrides.get("face", base.face),
        outfit=overrides.get("outfit", base.outfit),
        scene=overrides.get("scene", base.scene),
        framing=overrides.get("framing", base.framing),
        seed=int(overrides.get("seed", base.seed)),
        extra=overrides.get("extra", base.extra),
    )
