from arbscanner.adapters import BetfairDelayedAdapter, MatchbookAdapter


def test_exchange_adapters_expose_no_order_placement_methods():
    forbidden = {"place_order", "place_orders", "submit_offer", "submit_offers", "bet", "place_bet"}
    for cls in (BetfairDelayedAdapter, MatchbookAdapter):
        names = {n.lower() for n in dir(cls)}
        assert forbidden.isdisjoint(names)
