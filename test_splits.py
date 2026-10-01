import splits as s

P = ["Vikram", "Sivasankaran", "Vijay", "Guhan", "Aravind"]

def test_gas_equal():
    sh = s.equal_split(5000, P)
    assert sh == {p: 1000 for p in P}

def test_odd_cents_exact():
    sh = s.equal_split(5201, P)
    assert sum(sh.values()) == 5201 and max(sh.values()) - min(sh.values()) <= 1

def test_three_way_penny():
    sh = s.equal_split(5000, P[:3])
    assert sum(sh.values()) == 5000

def test_itemized_tax_tip_proportional():
    items = [{"name": "pasta", "price": 20.00, "people": ["Vikram"]},
             {"name": "pizza", "price": 30.00, "people": ["Vijay", "Guhan"]},
             {"name": "water", "price": 10.00, "people": []}]
    tax, tip = 500, 1200
    sh = s.itemized_split(items, tax, tip, P)
    total = 6000 + tax + tip
    s.check(sh, total)
    assert sh["Vikram"] > sh["Aravind"]   # ate more
    assert sh["Vijay"] == sh["Guhan"]

def test_check_catches_mismatch():
    try:
        s.check({"a": 100}, 101)
    except ValueError:
        return
    raise AssertionError
