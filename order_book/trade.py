from order_book.enums import Side, OrderType

class Trade:
    def __init__(self, price, quantity, buyer_order_id, seller_order_id,timestamp):
        self.price = price
        self.quantity = quantity
        self.buyer_order_id = buyer_order_id
        self.seller_order_id = seller_order_id
        self.timestamp = timestamp