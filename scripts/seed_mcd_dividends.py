"""
Adds McDonald's (MCD) to the test portfolio:
- 1 BUY in early 2022 (~30k EUR → ~125 shares at $265)
- Quarterly dividends 2022-Q3 through 2024-Q4
"""

import asyncio
import httpx

URL = "https://ghostfolio.jesuspb.dev"
TOKEN = "95e1af5e902c20ddfe15817f0715b84b0814551bec6231dd8f381dec47ea9c0a05ed23cdf0f494c7966193131dcdcabd32e09a5db6481199247da272babd84b8"
ACCOUNT_ID = "9fe76005-487b-43b3-af61-5ce7b57fa7b0"

# MCD quarterly dividend per share (USD):
#   2022: $1.38  |  2023: $1.52  |  2024: $1.67
SHARES = 125

ACTIVITIES = [
    # ── BUY — Jan 2022, ~30k EUR (~33k USD at 1.10 rate, $265/share) ────
    {
        "date": "2022-01-18T00:00:00.000Z",
        "symbol": "MCD", "type": "BUY",
        "quantity": SHARES, "unitPrice": 265.50, "fee": 1.00,
        "currency": "USD", "dataSource": "YAHOO",
    },

    # ── Dividends (quantity=1, unitPrice=total payout) ───────────────────
    # 2022
    {"date": "2022-09-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.38, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2022-12-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.38, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    # 2023
    {"date": "2023-03-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.52, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2023-06-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.52, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2023-09-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.52, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2023-12-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.52, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    # 2024
    {"date": "2024-03-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.67, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2024-06-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.67, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2024-09-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.67, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
    {"date": "2024-12-01T00:00:00.000Z", "symbol": "MCD", "type": "DIVIDEND",
     "quantity": 1, "unitPrice": round(SHARES * 1.67, 2), "fee": 0.00,
     "currency": "USD", "dataSource": "YAHOO"},
]


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{URL}/api/v1/auth/anonymous/{TOKEN}")
        r.raise_for_status()
        bearer = r.json()["authToken"]
        headers = {"Authorization": f"Bearer {bearer}"}
        print("Authenticated ✓")

        for a in ACTIVITIES:
            a["accountId"] = ACCOUNT_ID
            a["updateAccountBalance"] = False

        resp = await client.post(
            f"{URL}/api/v1/import",
            json={"activities": ACTIVITIES},
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            print(f"Failed ({resp.status_code}): {resp.text[:400]}")
        else:
            buy = 1
            divs = len(ACTIVITIES) - 1
            print(f"Imported: 1 BUY + {divs} DIVIDEND records for MCD ✓")
            total_div = sum(a["unitPrice"] for a in ACTIVITIES if a["type"] == "DIVIDEND")
            print(f"Total dividends imported: ${total_div:.2f} USD over 2022-2024")


if __name__ == "__main__":
    asyncio.run(main())
