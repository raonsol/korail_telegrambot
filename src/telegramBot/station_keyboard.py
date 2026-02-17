import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings

STATION_API_URL = "https://apis.data.go.kr/B551457/run/v2/codes2"
NUM_OF_ROWS = 6


async def search_stations(query: str, page: int = 1) -> dict:
    """공공데이터 API를 이용한 역 이름 검색

    Args:
        query: 검색할 역 이름 (부분 일치)
        page: 페이지 번호

    Returns:
        dict: {"stations": [{"code": "...", "name": "..."}], "total": int, "page": int}
    """
    params = {
        "serviceKey": settings.datagov_api_key,
        "pageNo": page,
        "numOfRows": NUM_OF_ROWS,
        "returnType": "JSON",
        "cond[type::EQ]": "stn_cd",
        "cond[value::LIKE]": query,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(STATION_API_URL, params=params)
        data = response.json()

    body = data["response"]["body"]
    items = body.get("items", {})
    item_list = items.get("item", []) if items else []

    stations = [{"code": item["code"], "name": item["value"]} for item in item_list]

    return {
        "stations": stations,
        "total": body.get("totalCount", 0),
        "page": body.get("pageNo", 1),
    }


def create_station_keyboard(
    stations: list[dict],
    action: str,
    query: str,
    page: int,
    total: int,
) -> InlineKeyboardMarkup:
    """검색 결과를 인라인 키보드로 생성

    Args:
        stations: [{"code": "...", "name": "..."}] 형태의 역 목록
        action: "station_src" 또는 "station_dst"
        query: 검색어 (페이지네이션용)
        page: 현재 페이지
        total: 전체 결과 수

    Returns:
        InlineKeyboardMarkup
    """
    keyboard = []

    # 역 버튼 (2열 배치)
    row = []
    for station in stations:
        row.append(
            InlineKeyboardButton(
                station["name"],
                callback_data=f"{action};{station['name']}",
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # 페이지네이션 버튼
    total_pages = (total + NUM_OF_ROWS - 1) // NUM_OF_ROWS
    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                "◀️ 이전",
                callback_data=f"stn_page;{action};{query};{page - 1}",
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            f"{page}/{total_pages}",
            callback_data="ignore",
        )
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                "다음 ▶️",
                callback_data=f"stn_page;{action};{query};{page + 1}",
            )
        )
    if total_pages > 1:
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)
