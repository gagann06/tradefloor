from order_book.enums import Side
from order_book.position import Position

SIDE_LOOKUP = {"buy": Side.BUY, "sell": Side.SELL}


def round_trips(fills):
    """Group a chronological list of fills into completed round trips."""
    position = Position()
    trips = []
    # ... accumulators for the trip currently open ...
    entry_cost = 0
    entry_quantity = 0
    exit_cost = 0
    exit_quantity = 0
    opened_at = None
    num_fills = 0

    def close_trip(closed_at):
        nonlocal entry_cost, entry_quantity, exit_cost, exit_quantity, num_fills, opened_at
        trips.append({"opened_at": opened_at, "closed_at": closed_at, "direction": direction, "quantity": entry_quantity, "entry_price": entry_cost / entry_quantity, "exit_price": exit_cost / exit_quantity, "pnl": position.realised_pnl - pnl_at_open, "fills": num_fills})
        entry_cost = entry_quantity = exit_cost = exit_quantity = 0
        num_fills = 0
        opened_at = None

        
    for fill in fills:
        before = position.quantity
        position.apply_fill(SIDE_LOOKUP[fill["side"]], fill["price"], fill["quantity"])
        after = position.quantity

        flipped = before != 0 and after != 0 and (before > 0) != (after > 0)

        if before == 0:                     # a trip just opened
            opened_at = fill["timestamp"]
            pnl_at_open = position.realised_pnl
            direction = "long" if after > 0 else "short"

        num_fills += 1
        signed = fill["quantity"] if fill["side"] == "buy" else -fill["quantity"]
        is_entry = before == 0 or (before > 0) == (signed > 0)

        if flipped:
            closing = min(fill["quantity"], abs(before))
            opening = fill["quantity"] - closing

            # Close trip
            exit_quantity += closing
            exit_cost += fill["price"] * closing
            close_trip(fill["timestamp"])

            # Open new trip
            opened_at = fill["timestamp"]
            pnl_at_open = position.realised_pnl
            direction = "long" if after > 0 else "short"

            entry_quantity += opening
            entry_cost += fill["price"] * opening
            num_fills = 1

        elif is_entry:
            entry_quantity += fill["quantity"]
            entry_cost     += fill["price"] * fill["quantity"]
        else:
            exit_quantity += fill["quantity"]
            exit_cost     += fill["price"] * fill["quantity"]

        if after == 0:                      # a trip just closed
            close_trip(fill["timestamp"])

    return trips