import heapq
from order_book.enums import Side, OrderType
from collections import deque
from order_book.order import Order
from order_book.trade import Trade

class OrderBook:
  def __init__(self):
      self.bid_prices_to_orders = {}
      self.bid_prices_heap = []
      self.ask_prices_to_orders = {}
      self.ask_prices_heap = []
      self.order_id_to_order = {}
      self.trade_log = []

  def best_ask(self):
    while self.ask_prices_heap != []:
       top = self.ask_prices_heap[0]
       if top in self.ask_prices_to_orders:
          return top
       else:
          heapq.heappop(self.ask_prices_heap)

    if self.ask_prices_heap == []:
       return None
       

  def best_bid(self):
    while self.bid_prices_heap != []:
      top = -self.bid_prices_heap[0]
      if top in self.bid_prices_to_orders:
        return top
      else:
        heapq.heappop(self.bid_prices_heap)

    if self.bid_prices_heap == []:
      return None

  def spread(self):
    if self.best_ask() == None or self.best_bid() == None:
      return None
    else:
      return self.best_ask() - self.best_bid()

  def bid_depth(self):
      result = {}
      for price, queue in self.bid_prices_to_orders.items():
          total = 0
          for o in queue:
              total += o.remaining_quantity
          result[price] = total
      return result

  def ask_depth(self):
      result = {}
      for price, queue in self.ask_prices_to_orders.items():
          total = 0
          for o in queue:
              total += o.remaining_quantity
          result[price] = total
      return result

  def crosses(self, o):
    if o.order_type == OrderType.LIMIT:
      if o.side == Side.BUY:
        crosses = self.best_ask() is not None and o.price >= self.best_ask()
      else:
        crosses = self.best_bid() is not None and o.price <= self.best_bid()
    else:
      if o.side == Side.BUY:
         crosses = self.best_ask() is not None
      else:
         crosses = self.best_bid() is not None
    return crosses
  
  def add_order(self, o:Order):
    trades = []

    crosses = self.crosses(o)

    while o.remaining_quantity > 0 and crosses:
      if o.side == Side.BUY:
        best_price = self.best_ask()
        queue = self.ask_prices_to_orders[best_price]
      else:
        best_price = self.best_bid()
        queue = self.bid_prices_to_orders[best_price]

      resting_order = queue[0]

      match_qty = min(o.remaining_quantity, resting_order.remaining_quantity)
      o.remaining_quantity -= match_qty
      resting_order.remaining_quantity -= match_qty

      price = resting_order.price
      quantity = match_qty

      if o.side == Side.BUY:
          buy_order_id, sell_order_id = o.order_id, resting_order.order_id
      else:
          buy_order_id, sell_order_id = resting_order.order_id, o.order_id
      trades.append(Trade(price, quantity, buy_order_id, sell_order_id, o.timestamp))

      if resting_order.remaining_quantity == 0:
        queue.popleft()
        if not queue:
          if o.side == Side.BUY:
              self.ask_prices_to_orders.pop(best_price)
          else:
              self.bid_prices_to_orders.pop(best_price)
        self.order_id_to_order.pop(resting_order.order_id)

      crosses = self.crosses(o)

    if o.remaining_quantity > 0:
      if o.order_type == OrderType.LIMIT:
        if o.side == Side.BUY:
          is_new_price_level = o.price not in self.bid_prices_to_orders
          queue = self.bid_prices_to_orders.setdefault(o.price, deque())
          queue.append(o)
          if is_new_price_level:
              heapq.heappush(self.bid_prices_heap, -o.price)
        else:
          is_new_price_level = o.price not in self.ask_prices_to_orders
          queue = self.ask_prices_to_orders.setdefault(o.price, deque())
          queue.append(o)
          if is_new_price_level:
              heapq.heappush(self.ask_prices_heap, o.price)

        self.order_id_to_order[o.order_id] = o

    self.trade_log.extend(trades)
    return trades


  def cancel_order(self, order_id):
    if order_id not in self.order_id_to_order:
      return False
    else:
      o = self.order_id_to_order[order_id]
      price = o.price

      if o.side == Side.BUY:
        queue = self.bid_prices_to_orders[price]
        queue.remove(o)
        if not queue:
          self.bid_prices_to_orders.pop(price)
        self.order_id_to_order.pop(order_id)

      else:
        queue = self.ask_prices_to_orders[price]
        queue.remove(o)
        if not queue:
          self.ask_prices_to_orders.pop(price)
        self.order_id_to_order.pop(order_id)

      return True