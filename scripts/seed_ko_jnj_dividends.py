"""
Adds KO and JNJ to cover the months MCD doesn't pay:
  KO  → Jan, Apr, Jul, Oct
  JNJ → Feb, May, Aug, Nov
  MCD → Mar, Jun, Sep, Dec  (already imported)

Together: every single month has dividend income.
"""

import asyncio
import httpx

URL = "https://ghostfolio.jesuspb.dev"
TOKEN = "95e1af5e902c20ddfe15817f0715b84b0814551bec6231dd8f381dec47ea9c0a05ed23cdf0f494c7966193131dcdcabd32e09a5db6481199247da272babd84b8"
ACCOUNT_ID = "9fe76005-487b-43b3-af61-5ce7b57fa7b0"

# ── KO (Coca-Cola) — 235 shares bought Mar 2021 @ $50 ≈ 10k EUR ─────────
KO_SHARES = 235
KO_DIV = {2021: 0.42, 2022: 0.44, 2023: 0.46, 2024: 0.485, 2025: 0.51, 2026: 0.535}

# ── JNJ (Johnson & Johnson) — 100 shares bought Apr 2021 @ $165 ≈ 14k EUR
JNJ_SHARES = 100
JNJ_DIV = {2021: 1.06, 2022: 1.13, 2023: 1.19, 2024: 1.24, 2025: 1.30, 2026: 1.30}


def div(symbol, shares, dps_map, months_years):
    """Build DIVIDEND activities. months_years = [(month, year), ...]"""
    out = []
    for month, year in months_years:
        dps = dps_map.get(year)
        if dps is None:
            continue
        out.append({
            "date": f"{year}-{month:02d}-01T00:00:00.000Z",
            "symbol": symbol, "type": "DIVIDEND",
            "quantity": 1, "unitPrice": round(shares * dps, 2),
            "fee": 0.0, "currency": "USD", "dataSource": "YAHOO",
            "accountId": ACCOUNT_ID, "updateAccountBalance": False,
        })
    return out


def buy(symbol, qty, price, date, currency="USD"):
    return {
        "date": date, "symbol": symbol, "type": "BUY",
        "quantity": qty, "unitPrice": price, "fee": 1.00,
        "currency": currency, "dataSource": "YAHOO",
        "accountId": ACCOUNT_ID, "updateAccountBalance": False,
    }


# KO: quarterly payments in Jan, Apr, Jul, Oct — starting Jul 2021
KO_PAYMENTS = [
    (7, 2021), (10, 2021),
    (1, 2022), (4, 2022), (7, 2022), (10, 2022),
    (1, 2023), (4, 2023), (7, 2023), (10, 2023),
    (1, 2024), (4, 2024), (7, 2024), (10, 2024),
    (1, 2025), (4, 2025), (7, 2025), (10, 2025),
    (1, 2026),
]

# JNJ: quarterly payments in Feb, May, Aug, Nov — starting Aug 2021
JNJ_PAYMENTS = [
    (8, 2021), (11, 2021),
    (2, 2022), (5, 2022), (8, 2022), (11, 2022),
    (2, 2023), (5, 2023), (8, 2023), (11, 2023),
    (2, 2024), (5, 2024), (8, 2024), (11, 2024),
    (2, 2025), (5, 2025), (8, 2025), (11, 2025),
    (2, 2026),
]

ACTIVITIES = [
    buy("KO",  KO_SHARES,  50.20, "2021-03-15T00:00:00.000Z"),
    buy("JNJ", JNJ_SHARES, 164.80, "2021-04-20T00:00:00.000Z"),
    *div("KO",  KO_SHARES,  KO_DIV,  KO_PAYMENTS),
    *div("JNJ", JNJ_SHARES, JNJ_DIV, JNJ_PAYMENTS),
]


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{URL}/api/v1/auth/anonymous/{TOKEN}")
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['authToken']}"}
        print(f"Authenticated ✓  ({len(ACTIVITIES)} activities to import)")

        chunk_size = 25
        imported = 0
        for i in range(0, len(ACTIVITIES), chunk_size):
            chunk = ACTIVITIES[i:i + chunk_size]
            resp = await client.post(f"{URL}/api/v1/import",
                                     json={"activities": chunk}, headers=headers)
            if resp.status_code not in (200, 201):
                print(f"  [!] chunk {i//chunk_size+1} failed ({resp.status_code}): {resp.text[:300]}")
            else:
                imported += len(chunk)
                print(f"  Chunk {i//chunk_size+1}: {len(chunk)} imported ✓")

        ko_divs  = sum(a["unitPrice"] for a in ACTIVITIES if a["symbol"] == "KO"  and a["type"] == "DIVIDEND")
        jnj_divs = sum(a["unitPrice"] for a in ACTIVITIES if a["symbol"] == "JNJ" and a["type"] == "DIVIDEND")
        print(f"\nDone — {imported}/{len(ACTIVITIES)} imported")
        print(f"  KO  total dividends: ${ko_divs:.2f}")
        print(f"  JNJ total dividends: ${jnj_divs:.2f}")
        print(f"\nCalendar coverage:")
        print(f"  Jan/Apr/Jul/Oct → KO | Feb/May/Aug/Nov → JNJ | Mar/Jun/Sep/Dec → MCD")


if __name__ == "__main__":
    asyncio.run(main())
