from order_book.enums import Side

class Position:
    def __init__(self):
        self.quantity = 0
        self.average_price = 0.0
        self.realised_pnl = 0.0

    def apply_fill(self, side, price, quantity):
        signed = quantity if side == Side.BUY else -quantity
        open_qty = abs(self.quantity)

        if self.quantity == 0 or (self.quantity > 0) == (signed > 0):
            self.average_price = ((price * quantity)  + (self.average_price * open_qty))/(quantity + open_qty)
            self.quantity += signed
            return
        else:
            closing = min(quantity, open_qty)
            if self.quantity > 0:
                self.realised_pnl += closing * (price - self.average_price)
            else:
                self.realised_pnl += closing * (self.average_price - price)
            self.quantity += signed

            leftover = quantity - closing

            if self.quantity == 0:
                self.average_price = 0.0
            elif leftover > 0:
                self.average_price = float(price)

    def unrealised_pnl(self, mark_price):
        if mark_price is None:
            return 0.0
        else:
            return (mark_price - self.average_price) * self.quantity
        
    def total_pnl(self, mark_price):
        return self.realised_pnl + self.unrealised_pnl(mark_price)