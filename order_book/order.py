import time
from order_book.enums import Side, OrderType

class Order:
    def __init__(self, order_id, side, price, order_type, original_quantity):

        if order_type not in(OrderType.LIMIT, OrderType.MARKET):
            raise ValueError("Orders can only be \"limit\" or \"market\"")

        if original_quantity <= 0:
            raise ValueError("Quantity must be positive")

        if order_type == OrderType.LIMIT:
            if price == None or price <= 0:
                raise ValueError("Price must be positive")
        else:
            if price != None:
                raise ValueError("Can't enter a price for a market order")

        if side not in (Side.BUY, Side.SELL):
            raise ValueError("You can only buy or sell")

        self.order_id = order_id
        self.side = side
        self.price = price
        self.order_type = order_type
        self.original_quantity = original_quantity
        self.remaining_quantity = original_quantity
        self.timestamp = time.time_ns()