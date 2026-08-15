from .models import Leg


def demo_opportunity():
    return {
        "event_key": "demo northbridge v riverside",
        "event_name": "Northbridge v Riverside",
        "market_name": "Match Odds",
        "event_start": None,
        "legs": [
            Leg("Matchbook", "Northbridge", 2.72, 420.0, 2.0, market_id="mb-demo", selection_id="1"),
            Leg("Betfair delayed", "Draw", 3.75, 265.0, 2.0, market_id="bf-demo", selection_id="2"),
            Leg("Matchbook", "Riverside", 3.05, 180.0, 2.0, market_id="mb-demo", selection_id="3"),
        ],
    }
