"""Tests for PriceLevel, the FIFO queue of orders resting at one price.

The book tests cover this indirectly through matching and cancellation, which is
what made the deque-to-linked-list swap safe. What they do not cover is the list
surgery itself: unlinking the head, the tail, the sole element, and whether the
level's own head/tail/size bookkeeping survives it. Those are the cases where an
intrusive linked list goes wrong silently - a corrupted level still looks fine
from the front and only misbehaves later.

Most tests here end in assert_consistent, which walks the level in both
directions. Forward traversal alone would miss a broken prev chain entirely.
"""

from order_book.enums import OrderType, Side
from order_book.order import Order
from order_book.price_level import PriceLevel


def make_order(order_id, price=100, side=Side.BUY, quantity=10):
    return Order(
        order_id=order_id,
        side=side,
        price=price,
        order_type=OrderType.LIMIT,
        original_quantity=quantity,
    )


def level_of(n):
    """A level holding n orders with ids 1..n, in insertion order."""
    level = PriceLevel()
    orders = [make_order(i) for i in range(1, n + 1)]
    for order in orders:
        level.append(order)
    return level, orders


def assert_consistent(level):
    """Check every invariant the level is supposed to maintain.

    Walking backwards from the tail is the point: a unlink that fixes `next` but
    not `prev` leaves a level that iterates correctly and is still broken.
    """
    forward = list(level)
    assert len(forward) == level.size

    if not forward:
        assert level.head is None
        assert level.tail is None
        return

    assert level.head is forward[0]
    assert level.tail is forward[-1]
    assert level.head.prev is None
    assert level.tail.next is None

    backward = []
    node = level.tail
    while node is not None:
        backward.append(node)
        node = node.prev

    assert backward == list(reversed(forward))


# --------------------------------------------------------------------------
# empty level
# --------------------------------------------------------------------------

def test_new_level_is_empty():
    level = PriceLevel()

    assert level.head is None
    assert level.tail is None
    assert len(level) == 0
    assert not level
    assert list(level) == []


def test_pop_front_on_empty_level_returns_none():
    level = PriceLevel()

    assert level.pop_front() is None
    assert_consistent(level)


# --------------------------------------------------------------------------
# append
# --------------------------------------------------------------------------

def test_single_append_is_both_head_and_tail():
    level = PriceLevel()
    order = make_order(1)

    level.append(order)

    assert level.head is order
    assert level.tail is order
    assert order.prev is None
    assert order.next is None
    assert len(level) == 1
    assert level
    assert_consistent(level)


def test_appends_preserve_insertion_order():
    level, orders = level_of(4)

    assert list(level) == orders
    assert len(level) == 4
    assert_consistent(level)


def test_append_links_neighbours_in_both_directions():
    level, orders = level_of(3)
    first, second, third = orders

    assert first.next is second
    assert second.prev is first
    assert second.next is third
    assert third.prev is second
    assert_consistent(level)


# --------------------------------------------------------------------------
# pop_front
# --------------------------------------------------------------------------

def test_pop_front_returns_orders_in_arrival_order():
    level, orders = level_of(3)

    popped = [level.pop_front() for _ in range(3)]

    assert popped == orders
    assert len(level) == 0
    assert_consistent(level)


def test_pop_front_on_sole_order_empties_the_level():
    level, (only,) = level_of(1)

    assert level.pop_front() is only
    assert level.head is None
    assert level.tail is None
    assert len(level) == 0
    assert not level
    assert_consistent(level)


def test_pop_front_clears_the_popped_orders_links():
    level, orders = level_of(3)

    popped = level.pop_front()

    assert popped.prev is None
    assert popped.next is None
    assert_consistent(level)


# --------------------------------------------------------------------------
# unlink - the operation cancellation depends on
# --------------------------------------------------------------------------

def test_unlink_head_promotes_the_next_order():
    level, (first, second, third) = level_of(3)

    level.unlink(first)

    assert level.head is second
    assert second.prev is None
    assert list(level) == [second, third]
    assert len(level) == 2
    assert_consistent(level)


def test_unlink_tail_demotes_to_the_previous_order():
    level, (first, second, third) = level_of(3)

    level.unlink(third)

    assert level.tail is second
    assert second.next is None
    assert list(level) == [first, second]
    assert len(level) == 2
    assert_consistent(level)


def test_unlink_middle_joins_its_neighbours():
    level, (first, second, third) = level_of(3)

    level.unlink(second)

    assert first.next is third
    assert third.prev is first
    assert list(level) == [first, third]
    assert level.head is first
    assert level.tail is third
    assert_consistent(level)


def test_unlink_sole_order_empties_the_level():
    level, (only,) = level_of(1)

    level.unlink(only)

    assert level.head is None
    assert level.tail is None
    assert len(level) == 0
    assert not level
    assert_consistent(level)


def test_unlink_clears_the_removed_orders_links():
    level, (first, second, third) = level_of(3)

    level.unlink(second)

    assert second.prev is None
    assert second.next is None


def test_unlink_every_order_front_to_back():
    level, orders = level_of(5)

    for order in orders:
        level.unlink(order)
        assert_consistent(level)

    assert len(level) == 0


def test_unlink_every_order_back_to_front():
    level, orders = level_of(5)

    for order in reversed(orders):
        level.unlink(order)
        assert_consistent(level)

    assert len(level) == 0


def test_unlink_from_the_middle_outwards():
    level, orders = level_of(5)
    first, second, third, fourth, fifth = orders

    for order in (third, second, fourth, first, fifth):
        level.unlink(order)
        assert_consistent(level)

    assert len(level) == 0


def test_unlink_and_pop_front_interleave_correctly():
    level, (first, second, third, fourth) = level_of(4)

    level.unlink(second)
    assert level.pop_front() is first
    level.unlink(fourth)

    assert list(level) == [third]
    assert level.head is third
    assert level.tail is third
    assert_consistent(level)


# --------------------------------------------------------------------------
# re-use
# --------------------------------------------------------------------------

def test_order_can_be_appended_again_after_being_unlinked():
    level, (first, second) = level_of(2)

    level.unlink(first)
    level.append(first)

    assert list(level) == [second, first]
    assert level.tail is first
    assert_consistent(level)


def test_level_can_be_refilled_after_being_emptied():
    level, orders = level_of(2)
    for order in orders:
        level.unlink(order)

    fresh = make_order(99)
    level.append(fresh)

    assert level.head is fresh
    assert level.tail is fresh
    assert len(level) == 1
    assert_consistent(level)


# --------------------------------------------------------------------------
# iteration
# --------------------------------------------------------------------------

def test_iteration_yields_orders_in_time_priority():
    level, orders = level_of(4)

    assert [order.order_id for order in level] == [1, 2, 3, 4]
    assert list(level) == orders


def test_iteration_survives_unlinking_the_order_being_visited():
    """__iter__ reads the next pointer before yielding, so a caller may unlink
    the order it was handed without the walk losing its place."""
    level, orders = level_of(4)

    seen = []
    for order in level:
        seen.append(order)
        level.unlink(order)

    assert seen == orders
    assert len(level) == 0
    assert_consistent(level)


# --------------------------------------------------------------------------
# protocol methods
# --------------------------------------------------------------------------

def test_len_and_truthiness_track_size():
    level = PriceLevel()
    assert len(level) == 0 and not level

    order = make_order(1)
    level.append(order)
    assert len(level) == 1 and level

    level.unlink(order)
    assert len(level) == 0 and not level


def test_level_equals_a_list_of_the_same_orders():
    level, orders = level_of(3)

    assert level == orders
    assert level == tuple(orders)
    assert level != orders[:2]


def test_level_equals_another_level_with_the_same_orders():
    level, orders = level_of(2)

    other = PriceLevel()
    for order in orders:
        other.append(order)

    assert level == other


def test_level_is_not_equal_to_an_unrelated_type():
    level, _ = level_of(1)

    assert (level == 5) is False
    assert (level == "not a level") is False


def test_repr_shows_the_contained_orders():
    level, _ = level_of(1)

    assert repr(level).startswith("PriceLevel([")


# --------------------------------------------------------------------------
# the property the whole class exists for
# --------------------------------------------------------------------------

def test_unlink_does_not_depend_on_position_in_the_level():
    """Cancellation is O(1) because the order carries its own neighbours. This
    asserts the behavioural half of that: unlinking the deepest order in a large
    level touches nothing but its two neighbours."""
    level, orders = level_of(1000)
    deepest = orders[-1]
    before = orders[-2]

    level.unlink(deepest)

    assert level.tail is before
    assert before.next is None
    assert len(level) == 999
    assert_consistent(level)


def test_orders_carry_link_fields_from_construction():
    """PriceLevel relies on prev/next existing on every Order, and Order uses
    __slots__ - so a field removed from the slots list would fail here rather
    than somewhere deep in the matching loop."""
    order = make_order(1)

    assert order.prev is None
    assert order.next is None
