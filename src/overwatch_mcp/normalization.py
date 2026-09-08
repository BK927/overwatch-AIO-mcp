"""Canonical public identifiers; never infer country or server from a player name."""

import re
import unicodedata

from .models import SourceError


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(c for c in value if c.isalnum())


HERO_NAMES_KO = {
    "ana": "아나",
    "anran": "안란",
    "ashe": "애쉬",
    "baptiste": "바티스트",
    "bastion": "바스티온",
    "brigitte": "브리기테",
    "cassidy": "캐서디",
    "dmon": "D.Mon",
    "domina": "도미나",
    "doomfist": "둠피스트",
    "dva": "D.Va",
    "echo": "에코",
    "emre": "엠레",
    "freja": "프레야",
    "genji": "겐지",
    "hazard": "해저드",
    "hanzo": "한조",
    "illari": "일리아리",
    "jetpack-cat": "제트팩 캣",
    "junker-queen": "정커퀸",
    "junkrat": "정크랫",
    "juno": "주노",
    "kiriko": "키리코",
    "lifeweaver": "라이프위버",
    "lucio": "루시우",
    "mauga": "마우가",
    "mei": "메이",
    "mercy": "메르시",
    "mizuki": "미즈키",
    "moira": "모이라",
    "orisa": "오리사",
    "pharah": "파라",
    "ramattra": "라마트라",
    "reaper": "리퍼",
    "reinhardt": "라인하르트",
    "roadhog": "로드호그",
    "shion": "시온",
    "sigma": "시그마",
    "sierra": "시에라",
    "sojourn": "소전",
    "soldier-76": "솔저: 76",
    "sombra": "솜브라",
    "symmetra": "시메트라",
    "torbjorn": "토르비욘",
    "tracer": "트레이서",
    "vendetta": "벤데타",
    "venture": "벤처",
    "widowmaker": "위도우메이커",
    "winston": "윈스턴",
    "wrecking-ball": "레킹볼",
    "wuyang": "우양",
    "zarya": "자리야",
    "zenyatta": "젠야타",
}
MAP_NAMES_KO = {
    "aatlis": "아틀리스",
    "antarctic-peninsula": "남극 반도",
    "anubis": "아누비스 신전",
    "ayutthaya": "아유타야",
    "black-forest": "검은 숲",
    "blizzard-world": "블리자드 월드",
    "busan": "부산",
    "castillo": "카스티요",
    "chateau-guillard": "샤토 기야르",
    "circuit-royal": "서킷 로얄",
    "colosseo": "콜로세오",
    "dorado": "도라도",
    "ecopoint-antarctica": "탐사 기지: 남극",
    "eichenwalde": "아이헨발데",
    "esperanca": "에스페란사",
    "hanamura": "하나무라",
    "hanaoka": "하나오카",
    "havana": "하바나",
    "hollywood": "할리우드",
    "horizon": "호라이즌 달 기지",
    "ilios": "일리오스",
    "junkertown": "쓰레기촌",
    "lijiang-tower": "리장 타워",
    "kanezaka": "카네자카",
    "kings-row": "왕의 길",
    "malevento": "말레벤토",
    "midtown": "미드타운",
    "necropolis": "네크로폴리스",
    "nepal": "네팔",
    "new-junk-city": "뉴 정크 시티",
    "new-queen-street": "뉴 퀸 스트리트",
    "numbani": "눔바니",
    "oasis": "오아시스",
    "paraiso": "파라이수",
    "paris": "파리",
    "petra": "페트라",
    "practice-range": "훈련장",
    "rialto": "리알토",
    "route-66": "66번 국도",
    "runasapi": "루나사피",
    "samoa": "사모아",
    "shambali-monastery": "샴발리 수도원",
    "suravasa": "수라바사",
    "throne-of-anubis": "아누비스의 왕좌",
    "volskaya": "볼스카야 인더스트리",
    "watchpoint-gibraltar": "감시 기지: 지브롤터",
    "workshop-chamber": "워크샵 방",
    "workshop-expanse": "워크샵 개활지",
    "workshop-green-screen": "워크샵 그린 스크린",
    "workshop-island": "워크샵 섬",
    "wuxing-university": "우싱 대학",
}
HERO_ALIASES = {_key(alias): slug for slug, ko in HERO_NAMES_KO.items() for alias in (slug, ko)}
HERO_ALIASES.update(
    {
        _key(k): v
        for k, v in {
            "라인": "reinhardt",
            "rein": "reinhardt",
            "라마": "ramattra",
            "디바": "dva",
            "디몬": "dmon",
            "맥크리": "cassidy",
            "mccree": "cassidy",
            "솔저": "soldier-76",
            "soldier": "soldier-76",
            "호그": "roadhog",
            "위도우": "widowmaker",
            "둠피": "doomfist",
            "햄찌": "wrecking-ball",
            "hammond": "wrecking-ball",
            "정퀸": "junker-queen",
            "jq": "junker-queen",
            "볼": "wrecking-ball",
            "all-heroes": "all-heroes",
            "전체영웅": "all-heroes",
        }.items()
    }
)
MAP_ALIASES = {_key(alias): slug for slug, ko in MAP_NAMES_KO.items() for alias in (slug, ko)}
MAP_ALIASES.update(
    {
        _key(k): v
        for k, v in {
            "temple-of-anubis": "anubis",
            "horizon-lunar-colony": "horizon",
            "volskaya-industries": "volskaya",
            "지브롤터": "watchpoint-gibraltar",
            "샴발리": "shambali-monastery",
            "all": "all-maps",
            "all-maps": "all-maps",
            "전체": "all-maps",
            "전체전장": "all-maps",
        }.items()
    }
)
TIERS = (
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHAMPION",
)
REGIONS = ("ASIA", "AMERICAS", "EUROPE", "KR")


def _text(value: str, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceError(
            "UNSUPPORTED_FILTER", f"{kind} must be a nonempty string.", "normalization"
        )
    return value.strip()


def _slug(value: str, aliases: dict[str, str], kind: str) -> str:
    value = _text(value, kind)
    if _key(value) in aliases:
        return aliases[_key(value)]
    # New English source identifiers remain usable without a package update.
    slug = re.sub(r"[\s_]+", "-", value.lower())
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise SourceError("UNSUPPORTED_FILTER", f"Unknown {kind} alias: {value}", "normalization")
    return slug


def normalize_hero(value: str) -> str:
    return _slug(value, HERO_ALIASES, "hero")


def normalize_map(value: str) -> str:
    return _slug(value, MAP_ALIASES, "map")


def _enum(value: str, mapping: dict[str, str], kind: str) -> str:
    value = _text(value, kind)
    result = mapping.get(_key(value))
    if result is None:
        raise SourceError("UNSUPPORTED_FILTER", f"Unsupported {kind}: {value}", "normalization")
    return result


def normalize_region(value: str) -> str:
    return _enum(
        value,
        {
            _key(k): v
            for k, v in {
                "asia": "ASIA",
                "아시아": "ASIA",
                "americas": "AMERICAS",
                "amer": "AMERICAS",
                "america": "AMERICAS",
                "na": "AMERICAS",
                "북미": "AMERICAS",
                "미주": "AMERICAS",
                "europe": "EUROPE",
                "eu": "EUROPE",
                "유럽": "EUROPE",
                "kr": "KR",
                "korea": "KR",
                "한국": "KR",
                "대한민국": "KR",
            }.items()
        },
        "region",
    )


def normalize_tier(value: str) -> str:
    aliases = dict(
        zip(
            (
                "브론즈",
                "실버",
                "골드",
                "플래티넘",
                "에메랄드",
                "다이아몬드",
                "마스터",
                "그랜드마스터",
                "챔피언",
            ),
            TIERS,
            strict=True,
        )
    )
    aliases.update({tier: tier for tier in TIERS})
    aliases.update(
        {
            "all": "ALL",
            "전체": "ALL",
            "gm": "GRANDMASTER",
            "plat": "PLATINUM",
            "플래티나": "PLATINUM",
            "다이아": "DIAMOND",
            "그마": "GRANDMASTER",
            "마스터즈": "MASTER",
        }
    )
    return _enum(value, {_key(k): v for k, v in aliases.items()}, "tier")


def normalize_platform(value: str) -> str:
    return _enum(
        value,
        {
            _key(k): v
            for k, v in {
                "pc": "pc",
                "컴퓨터": "pc",
                "console": "console",
                "콘솔": "console",
            }.items()
        },
        "platform",
    )


def normalize_mode(value: str) -> str:
    return _enum(
        value,
        {
            _key(k): v
            for k, v in {
                "competitive": "competitive",
                "경쟁전": "competitive",
                "ranked": "competitive",
                "quickplay": "quickplay",
                "quick-play": "quickplay",
                "빠른대전": "quickplay",
                "일반전": "quickplay",
            }.items()
        },
        "mode",
    )


def normalize_role(value: str) -> str:
    return _enum(
        value,
        {
            _key(k): v
            for k, v in {
                "tank": "tank",
                "탱커": "tank",
                "돌격": "tank",
                "탱": "tank",
                "damage": "damage",
                "dps": "damage",
                "공격": "damage",
                "딜러": "damage",
                "딜": "damage",
                "support": "support",
                "지원": "support",
                "힐러": "support",
                "힐": "support",
            }.items()
        },
        "role",
    )


def normalize_player_id(value: str) -> str:
    """Only replace the BattleTag separator; case is significant to Blizzard."""
    value = _text(value, "player_id")
    if len(value) > 256 or any(ord(c) < 32 for c in value) or "/" in value or "\\" in value:
        raise SourceError("UNSUPPORTED_FILTER", "Invalid player identifier.", "normalization")
    return value.replace("#", "-")


def battle_tag_from_id(value: str) -> str | None:
    """Hexadecimal Blizzard identifiers are not BattleTags."""
    match = re.fullmatch(r"(.+)-(\d{4,})", value)
    return f"{match[1]}#{match[2]}" if match else None
