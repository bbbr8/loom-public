"""Exact, bounded phase-oracle circuits over Boolean model assignments.

Amplitudes are rational coefficients divided by sqrt(2**n). This is a classical
simulation of a uniform quantum state, phase oracle, and Grover diffusion.
"""
from fractions import Fraction as F


def rational(value):
    value = F(value)
    return {"numerator": value.numerator, "denominator": value.denominator,
            "exact": str(value)}


def validate_iterations(iterations):
    if iterations is not None and (type(iterations) is not int or not 0 <= iterations <= 16):
        raise ValueError("Grover iterations must be 0..16")


def circuit(bits, marked, iterations=None):
    if type(bits) is not int or not 1 <= bits <= 8:
        raise ValueError("quantum reasoning supports 1..8 assumption wires")
    size = 1 << bits
    try:
        marked = set(marked)
    except TypeError as e:
        raise ValueError("phase oracle indices must be integers") from e
    if any(type(x) is not int or not 0 <= x < size for x in marked):
        raise ValueError("phase oracle indices are outside the register")
    validate_iterations(iterations)
    coeff = [F(1) for _ in range(size)]
    history = [F(len(marked), size)]
    # Stop after detecting the first peak. The rejected look-ahead step counts
    # as selection work, but is not part of the selected circuit or its history.
    limit = 16 if iterations is None else iterations
    steps = 0
    for _ in range(limit):
        phased = [-a if i in marked else a for i, a in enumerate(coeff)]
        mean = sum(phased) / size
        candidate = [2 * mean - a for a in phased]
        assert sum(a*a for a in candidate) == size
        share = sum(candidate[i]**2 for i in marked) / size
        steps += 1
        if iterations is None and share <= history[-1]:
            break
        coeff = candidate
        history.append(share)
    selected = len(history) - 1
    # H^n O H^n |0>: amplitudes are the normalized Walsh transform of
    # the oracle signs. This is a separate circuit, not the Grover output.
    # Integer Walsh butterflies preserve exact amplitudes in O(n * 2**n).
    transform = [-1 if x in marked else 1 for x in range(size)]
    width = 1
    while width < size:
        for start in range(0, size, 2 * width):
            for offset in range(width):
                left, right = start + offset, start + offset + width
                a, b = transform[left], transform[right]
                transform[left], transform[right] = a + b, a - b
        width *= 2
    walsh = []
    for mask, value in enumerate(transform):
        amplitude = F(value, size)
        walsh.append({"mask": format(mask, "0%db" % bits),
                      "amplitude": rational(amplitude),
                      "measurement_share": rational(amplitude*amplitude)})
    assert sum(F(x["measurement_share"]["exact"]) for x in walsh) == 1
    return {"wires": bits, "basis_states": size, "marked_states": len(marked),
            "iterations": selected, "iteration_selection": "first non-increasing peak in 0..16" if iterations is None else "caller-selected",
            "selection_simulated_steps": steps,
            "oracle_applications": selected,
            "initial_marked_share": rational(history[0]),
            "final_marked_share": rational(history[selected]),
            "history": [rational(x) for x in history[:selected+1]],
            "state": [{"bits": format(i, "0%db" % bits), "marked": i in marked,
                       "amplitude_coefficient": rational(a),
                       "amplitude_denominator": "sqrt(%d)" % size,
                       "measurement_share": rational(a*a/size)} for i, a in enumerate(coeff)],
            "interference": {"circuit": "H^n, phase oracle, H^n", "oracle_applications": 1,
                             "spectrum": walsh}}
