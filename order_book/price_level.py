class PriceLevel:
  """The FIFO queue of orders resting at a single price.

  Orders are linked through their own prev/next fields rather than being held
  in a separate container, so an order handed over by the order-ID index can be
  unlinked in O(1) without searching. A deque cannot do that: it is O(1) at both
  ends but removing from the middle means scanning for the position first, and
  a cancellation only ever knows the order, never where it sits in the level.

  Time priority is the insertion order: append at the tail, match from the head.
  """

  __slots__ = ("head", "tail", "size")

  def __init__(self):
    self.head = None
    self.tail = None
    self.size = 0

  def append(self, o):
    """Rest an order at the back of the queue."""
    o.prev = self.tail
    o.next = None

    if self.tail is None:
      self.head = o
    else:
      self.tail.next = o

    self.tail = o
    self.size += 1

  def pop_front(self):
    """Remove and return the order at the front - the next one to be matched."""
    o = self.head
    if o is None:
      return None
    self.unlink(o)
    return o

  def unlink(self, o):
    """Detach an order from the level.

    The order carries its own neighbours, so this is pointer surgery with no
    scan. The caller is responsible for the order actually being in this level -
    every caller reaches it through the order-ID index, which only holds resting
    orders.
    """
    if o.prev is None:
      self.head = o.next
    else:
      o.prev.next = o.next

    if o.next is None:
      self.tail = o.prev
    else:
      o.next.prev = o.prev

    o.prev = None
    o.next = None
    self.size -= 1

  def __iter__(self):
    o = self.head
    while o is not None:
      following = o.next
      yield o
      o = following

  def __len__(self):
    return self.size

  def __bool__(self):
    return self.size > 0

  def __eq__(self, other):
    """Compare against a plain sequence, so tests can assert on level contents."""
    if isinstance(other, PriceLevel):
      return list(self) == list(other)
    if isinstance(other, (list, tuple)):
      return list(self) == list(other)
    return NotImplemented

  def __repr__(self):
    return f"PriceLevel({list(self)!r})"
