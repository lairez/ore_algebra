# -*- coding: utf-8 - vim: tw=80
r"""
Local solutions
"""

import collections, logging

from sage.arith.all import lcm
from sage.categories.pushout import pushout
from sage.misc.cachefunc import cached_method
from sage.misc.lazy_attribute import lazy_attribute
from sage.modules.free_module_element import vector
from sage.rings.all import ZZ, QQ, QQbar, RBF, RealBallField, ComplexBallField
from sage.rings.complex_arb import ComplexBall
from sage.rings.integer import Integer
from sage.rings.number_field.number_field import NumberField_absolute
from sage.rings.polynomial import polynomial_element
from sage.rings.polynomial.polynomial_ring_constructor import PolynomialRing
from sage.structure.sequence import Sequence
from sage.symbolic.all import SR, pi, I

from .. import ore_algebra
from . import utilities

from .shiftless import dispersion, my_shiftless_decomposition

logger = logging.getLogger(__name__)

##############################################################################
# Recurrence relations
##############################################################################

def bw_shift_rec(dop, shift=ZZ.zero(), clear_denominators=False):
    Scalars = pushout(dop.base_ring().base_ring(), shift.parent())
    Pols_x = dop.base_ring().change_ring(Scalars)
    Pols_n, n = PolynomialRing(Scalars, 'n').objgen()
    Rops = ore_algebra.OreAlgebra(Pols_n, 'Sn')
    # Using the primitive part here would break the computation of residuals!
    # TODO: add test (arctan); better fix?
    # Other interesting cases: operators of the form P(Θ) (with constant
    # coefficients)
    #rop = dop.to_S(Rops).primitive_part().numerator()
    rop = dop.change_ring(Pols_x).to_S(Rops)
    if clear_denominators:
        den = lcm([p.denominator() for p in rop])
        rop = den*rop
    ordrec = rop.order()
    coeff = [rop[ordrec-k](n-ordrec+shift)
             for k in xrange(ordrec+1)]
    return BwShiftRec(coeff)

class BwShiftRec(object):
    r"""
    A recurrence relation, written in terms of the backward shift operator.

    This class is mainly intended to provide reasonably fast evaluation in the
    context of naïve unrolling.
    """

    def __init__(self, coeff):
        assert isinstance(coeff[0], polynomial_element.Polynomial)
        self.coeff = coeff
        self.base_ring = coeff[0].parent()
        self.Scalars = self.base_ring.base_ring()
        self.order = len(coeff) - 1

    def __repr__(self):
        n = self.base_ring.variable_name()
        return " + ".join("({})*S{}^(-{})".format(c, n, j)
                          for j, c in enumerate(self.coeff))

    @cached_method
    def eval_method(self, tgt):
        if utilities.is_QQi(self.Scalars) and isinstance(tgt, ComplexBallField):
            ZZn = PolynomialRing(ZZ, 'n')
            re_im = [
                    (ZZn([c.real() for c in pol]), ZZn([c.imag() for c in pol]))
                    for pol in self.coeff]
            if utilities.has_new_ComplexBall_constructor():
                def ev(point):
                    return [ComplexBall(tgt, re(point), im(point))
                            for re, im in re_im]
            else:
                def ev(point):
                    return [tgt(re(point), im(point)) for re, im in re_im]
        else:
            def ev(point):
                return [tgt(pol(point)) for pol in self.coeff]
        return ev

    @cached_method
    def _coeff_series(self, i, j):
        p = self.coeff[i]
        return self.base_ring([ZZ(k+j).binomial(k)*p[k+j]
                               for k in xrange(p.degree() + 1 - j)])

    @cached_method
    def scalars_embedding(self, tgt):
        if isinstance(self.Scalars, NumberField_absolute):
            # do complicated coercions via QQbar and CLF only once...
            Pol = PolynomialRing(tgt, 'x')
            x = tgt(self.Scalars.gen())
            return lambda elt: Pol([tgt(c) for c in elt._coefficients()])(x)
        else:
            return tgt

    def eval_series(self, tgt, point, ord):
        mor = self.scalars_embedding(tgt)
        point = self.Scalars(point)
        return [[mor(self._coeff_series(i,j)(point)) for j in xrange(ord)]
                for i in xrange(len(self.coeff))]

    def eval_inv_lc_series(self, point, ord, shift):
        ser = self.base_ring( # polynomials, viewed as jets
                [self._coeff_series(0, j)(point) for j in xrange(shift, ord)])
        return ser.inverse_series_trunc(ord)

    def __getitem__(self, i):
        return self.coeff[i]

    def shift(self, sh):
        n = self.coeff[0].parent().gen()
        return BwShiftRec([pol(sh + n) for pol in self.coeff])

    def change_base(self, base):
        if base is self.base_ring:
            return self
        return BwShiftRec([pol.change_ring(base) for pol in self.coeff])

    def lc_as_rec(self):
        return BwShiftRec([self.coeff[0]])

class LogSeriesInitialValues(object):
    r"""
    Initial values defining a logarithmic series.

    - ``self.expo`` is an algebraic number representing the “valuation” of the
      log-series,
    - ``self.shift`` is a dictionary mapping an integer shift s to a tuple of
      initial values corresponding to the coefficients of x^s, x^s·log(x), ...,
      x^s·log(x)^k/k! for some k
    """

    def __init__(self, expo, values, dop=None, check=True):
        r"""
        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.naive_sum import *
            sage: Dops, x, Dx = DifferentialOperators()
            sage: LogSeriesInitialValues(0, {0: (1, 0)}, x*Dx^3 + 2*Dx^2 + x*Dx)
            Traceback (most recent call last):
            ...
            ValueError: invalid initial data for x*Dx^3 + 2*Dx^2 + x*Dx at 0
        """
        try:
            self.expo = QQ.coerce(expo)
        except TypeError:
            try:
                self.expo = QQbar.coerce(expo)
            except TypeError:
                # symbolic; won't be sortable
                self.expo = expo
        if isinstance(values, dict):
            all_values = sum(values.values(), ()) # concatenation of tuples
        else:
            all_values = values
            values = dict((n, (values[n],)) for n in xrange(len(values)))
        self.universe = Sequence(all_values).universe()
        if not utilities.is_numeric_parent(self.universe):
            raise ValueError("initial values must coerce into a ball field")
        self.shift = { s: tuple(self.universe(a) for a in ini)
                       for s, ini in values.iteritems() }

        try:
            if check and dop is not None and not self.is_valid_for(dop):
                raise ValueError("invalid initial data for {} at 0".format(dop))
        except TypeError: # coercion problems btw QQbar and number fields
            pass

    def __repr__(self):
        return ", ".join(
            "[z^({expo}+{shift})·log(z)^{log_power}/{log_power}!] = {val}"
            .format(expo=self.expo, shift=s, log_power=log_power, val=val)
            for s, ini in self.shift.iteritems()
            for log_power, val in enumerate(ini))

    def is_valid_for(self, dop):
        ind = dop.indicial_polynomial(dop.base_ring().gen())
        for sl_factor, shifts in my_shiftless_decomposition(ind):
            for k, (val_shift, _) in enumerate(shifts):
                if sl_factor(self.expo - val_shift).is_zero():
                    if len(self.shift) != len(shifts) - k:
                        return False
                    for shift, mult in shifts[k:]:
                        if len(self.shift.get(shift - val_shift, ())) != mult:
                            return False
                    return True
        return False

    def is_real(self, dop):
        r"""
        Try to detect cases where the coefficients of the series will be real.

        TESTS::

            sage: from ore_algebra import *
            sage: Dops, x, Dx = DifferentialOperators()
            sage: i = QuadraticField(-1, 'i').gen()
            sage: (x^2*Dx^2 + x*Dx + 1).numerical_transition_matrix([0, 1/2])
            [ [0.769238901363972...] + [0.638961276313634...]*I [0.769238901363972...] + [-0.6389612763136...]*I]
            sage: (Dx-i).numerical_transition_matrix([0,1])
            [[0.540302305868139...] + [0.841470984807896...]*I]
        """
        # We check that the exponent is real to ensure that the coefficients
        # will stay real. Note however that we don't need to make sure that
        # pt^expo*log(z)^k is real.
        return (utilities.is_real_parent(dop.base_ring().base_ring())
                and utilities.is_real_parent(self.universe)
                and self.expo.imag().is_zero())

    def accuracy(self):
        infinity = RBF.maximal_accuracy()
        if self.universe.is_exact():
            return infinity
        elif isinstance(self.universe, (RealBallField, ComplexBallField)):
            return min(infinity, *(x.accuracy()
                                   for val in self.shift.itervalues()
                                   for x in val))
        else:
            raise ValueError

def random_ini(dop):
    import random
    from sage.all import VectorSpace
    ind = dop.indicial_polynomial(dop.base_ring().gen())
    sl_decomp = my_shiftless_decomposition(ind)
    pol, shifts = random.choice(sl_decomp)
    expo = random.choice(pol.roots(QQbar))[0]
    expo = utilities.as_embedded_number_field_element(expo)
    values = {}
    while all(a.is_zero() for v in values.values() for a in v):
        values = {
            shift: tuple(VectorSpace(QQ, mult).random_element(10))
            for shift, mult in shifts
        }
    return LogSeriesInitialValues(expo, values, dop)

##############################################################################
# Structure of the local basis at a regular singular point
##############################################################################

_FundamentalSolution0 = collections.namedtuple(
    'FundamentalSolution',
    ['leftmost', 'shift', 'log_power', 'value'])

class FundamentalSolution(_FundamentalSolution0):
    @lazy_attribute
    def valuation(self):
        return QQbar(self.leftmost + self.shift) # alg vs NFelt for re, im

def sort_key_by_asympt(sol):
    r"""
    Specify the sorting order for local solutions.

    Roughly speaking, they are sorted in decreasing order of asymptotic
    dominance: when two solutions are asymptotically comparable, the largest
    one as x → 0 comes first. In addition, divergent solutions, including
    things like `x^i`, always come before convergent ones.
    """
    re, im = sol.valuation.real(), sol.valuation.imag()
    return re, -sol.log_power, -im.abs(), im.sign()

class LocalBasisMapper(object):
    r"""
    Utility class for iterating over the canonical local basis of solutions of
    an operator.

    Subclasses should define a fun() method that takes as input a
    LogSeriesInitialValues structure and can access the iteration variables
    as well as some derived quantities through the instance's field.

    The nested loops that iterate over the solutions are spread over several
    methods that can be overriden to share parts of the computation in a
    class of related solutions. The choice of unconditional computations,
    exported data and hooks is a bit ad hoc.
    """

    def __init__(self, dop):
        self.dop = dop

    def run(self):
        r"""
        Compute self.fun() for each element of the local basis at 0 of self.dop.

        The output is a list of FundamentalSolution structures, sorted in the
        canonical order.
        """

        self.bwrec = bw_shift_rec(self.dop) # XXX wasteful in binsplit case
        ind = self.bwrec[0]
        self.sl_decomp = my_shiftless_decomposition(ind)
        logger.debug("indicial polynomial = %s (shiftless decomposition = %s)",
                     ind, self.sl_decomp)

        self.process_decomposition()

        self.cols = []
        self.nontrivial_factor_index = 0
        for self.sl_factor, self.shifts in self.sl_decomp:
            for self.irred_factor, irred_mult in self.sl_factor.factor():
                assert irred_mult == 1
                roots = self.irred_factor.roots(QQbar, multiplicities=False)
                self.roots = [utilities.as_embedded_number_field_element(rt)
                              for rt in roots]
                logger.debug("indicial factor = %s, roots = %s",
                             self.irred_factor, self.roots)
                self.irred_factor_cols = []
                self.process_irred_factor()
                self.cols.extend(self.irred_factor_cols)
                if self.irred_factor.degree() >= 2:
                    self.nontrivial_factor_index += 1
        self.cols.sort(key=sort_key_by_asympt)
        return self.cols

    def process_decomposition(self):
        pass

    # The next three methods can be overridden to customize the iteration. Each
    # specialized implementation should set the same fields (self.leftmost,
    # etc.) as the original method does, and call the next method in the list,
    # or at least ultimately result in process_solution() being called with the
    # correct fields set.

    def process_irred_factor(self):
        for self.leftmost in self.roots:
            self.process_modZ_class()

    def process_modZ_class(self):
        self.shifted_bwrec = self.bwrec.shift(self.leftmost)
        for self.shift, self.mult in self.shifts:
            self.process_valuation()

    def process_valuation(self):
        for self.log_power in xrange(self.mult):
            self.process_solution()

    def process_solution(self):
        logger.info(r"solution z^(%s+%s)·log(z)^%s/%s! + ···",
                    self.leftmost, self.shift,
                    self.log_power, self.log_power)
        ini = LogSeriesInitialValues(
            dop = self.dop,
            expo = self.leftmost,
            values = {
                s: tuple(ZZ.one() if (s, p) == (self.shift, self.log_power)
                            else ZZ.zero()
                            for p in xrange(m))
                for s, m in self.shifts},
            check = False)
        # XXX: inefficient if self.shift >> 0
        value = self.fun(ini)
        sol = FundamentalSolution(
            leftmost = self.leftmost,
            shift = ZZ(self.shift),
            log_power = ZZ(self.log_power),
            value = value)
        logger.debug("value = %s", sol)
        self.irred_factor_cols.append(sol)

    def fun(self, ini):
        return None

def exponent_shifts(dop, leftmost):
    bwrec = bw_shift_rec(dop)
    ind = bwrec[0]
    sl_decomp = my_shiftless_decomposition(ind)
    cand = [shifts for fac, shifts in sl_decomp if fac(leftmost).is_zero()]
    assert len(cand) == 1
    shifts = [s for s in cand[0] if s >= 0]
    assert shifts[0][0] == 0
    return shifts

def log_series(ini, bwrec, order):
    Coeffs = pushout(bwrec.base_ring.base_ring(), ini.universe)
    log_prec = sum(len(v) for v in ini.shift.itervalues())
    precomp_len = max(1, bwrec.order) # hack for recurrences of order zero
    bwrec_nplus = collections.deque(
            (bwrec.eval_series(Coeffs, i, log_prec)
                for i in xrange(precomp_len)),
            maxlen=precomp_len)
    series = []
    for n in xrange(order):
        new_term = vector(Coeffs, log_prec)
        mult = len(ini.shift.get(n, ()))
        for p in xrange(log_prec - mult - 1, -1, -1):
            combin  = sum(bwrec_nplus[0][i][j]*series[-i][p+j]
                          for j in xrange(log_prec - p)
                          for i in xrange(min(bwrec.order, n), 0, -1))
            combin += sum(bwrec_nplus[0][0][j]*new_term[p+j]
                          for j in xrange(mult + 1, log_prec - p))
            new_term[mult + p] = - ~bwrec_nplus[0][0][mult] * combin
        for p in xrange(mult - 1, -1, -1):
            new_term[p] = ini.shift[n][p]
        series.append(new_term)
        bwrec_nplus.append(bwrec.eval_series(Coeffs, n+precomp_len, log_prec))
    return series

def log_series_value(Jets, derivatives, expo, psum, pt, branch):
    r"""
    Evaluate a logarithmic series.

    That is, compute ::

        (pt + η)^expo * Σ_k (psum[k]*log(pt + η)^k/k!) + O(η^derivatives),

    as an element of ``Jets``, optionally using a non-standard branch of the
    logarithm.

    * ``branch`` - branch of the logarithm to use; ``(0,)`` means the standard
      branch, ``(k,)`` means log(z) + 2kπi, a tuple of length > 1 averages over
      the corresponding branches

    Note that while this function computes ``pt^expo`` in ℂ, it does NOT
    specialize abstract algebraic numbers that might appear in ``psum``.
    """
    log_prec = psum.length()
    if log_prec > 1 or expo not in ZZ or branch != (0,):
        pt = pt.parent().complex_field()(pt)
        Jets = Jets.change_ring(Jets.base_ring().complex_field())
        psum = psum.change_ring(Jets)
    high = Jets([0] + [(-1)**(k+1)*~pt**k/k
                       for k in xrange(1, derivatives)])
    aux = high*expo
    logger.debug("aux=%s", aux)
    val = Jets.base_ring().zero()
    for b in branch:
        twobpii = pt.parent()(2*b*pi*I)
        # hardcoded series expansions of log(a+η) and (a+η)^λ
        # (too cumbersome to compute directly in Sage at the moment)
        logpt = Jets([pt.log() + twobpii]) + high
        logger.debug("logpt[%s]=%s", b, logpt)
        inipow = ((twobpii*expo).exp()*pt**expo
                *sum(_pow_trunc(aux, k, derivatives)/Integer(k).factorial()
                    for k in xrange(derivatives)))
        logger.debug("inipow[%s]=%s", b, inipow)
        val += inipow.multiplication_trunc(
                sum(psum[p]._mul_trunc_(_pow_trunc(logpt, p, derivatives),
                                        derivatives)
                        /Integer(p).factorial()
                    for p in xrange(log_prec)),
                derivatives)
    val /= len(branch)
    return val

def _pow_trunc(a, n, ord):
    pow = a.parent().one()
    pow2k = a
    while n:
        if n & 1:
            pow = pow._mul_trunc_(pow2k, ord)
        pow2k = pow2k._mul_trunc_(pow2k, ord)
        n = n >> 1
    return pow

