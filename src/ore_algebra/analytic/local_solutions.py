# -*- coding: utf-8 - vim: tw=80
r"""
Local solutions
"""

from six.moves import range

import collections, logging

from itertools import chain

from sage.arith.all import gcd, lcm
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

from .differential_operator import DifferentialOperator
from .shiftless import dispersion, my_shiftless_decomposition

logger = logging.getLogger(__name__)

##############################################################################
# Recurrence relations
##############################################################################

def bw_shift_rec(dop, shift=ZZ.zero(), clear_denominators=False):
    Scalars = pushout(dop.base_ring().base_ring(), shift.parent())
    if dop.parent().is_D():
        dop = DifferentialOperator(dop) # compatibility bugware
        rop = dop._my_to_S()
    else: # more compatibility bugware
        Pols_n = PolynomialRing(dop.base_ring().base_ring(), 'n')
        rop = dop.to_S(ore_algebra.OreAlgebra(Pols_n, 'Sn'))
    Pols_n, n = rop.base_ring().change_ring(Scalars).objgen()
    rop = rop.change_ring(Pols_n)
    if clear_denominators:
        den = lcm([p.denominator() for p in rop])
        rop = den*rop
    # Remove constant common factors to make the recurrence smaller
    if Scalars is QQ:
        g = gcd(c for p in rop for c in p)
    # elif utilities.is_QQi(Scalars): # XXX: too slow (and not general enough)
    #     ZZi = Scalars.maximal_order()
    #     g = ZZi.zero()
    #     for c in (c1 for p in rop for c1 in p):
    #         g = ZZi(g).gcd(ZZi(c)) # gcd returns a nfe
    #         if g.is_one():
    #             g = None
    #             break
    else:
        g = None
    if g is not None:
        rop = (1/g)*rop
    ordrec = rop.order()
    coeff = [rop[ordrec-k](n-ordrec+shift)
             for k in range(ordrec+1)]
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
        if (utilities.is_QQi(self.Scalars)
                and isinstance(tgt, ComplexBallField)
                and utilities.has_new_ComplexBall_constructor()):

            ZZn = PolynomialRing(ZZ, 'n')
            re_im = [
                    (ZZn([c.real() for c in pol]), ZZn([c.imag() for c in pol]))
                    for pol in self.coeff]
            def ev(point):
                return [ComplexBall(tgt, re(point), im(point))
                        for re, im in re_im]
        else:
            def ev(point):
                return [tgt(pol(point)) for pol in self.coeff]
        return ev

    @cached_method
    def _coeff_series(self, i, j):
        p = self.coeff[i]
        return self.base_ring([ZZ(k+j).binomial(k)*p[k+j]
                               for k in range(p.degree() + 1 - j)])

    @cached_method
    def _coeff_series_re_im(self, i, j):
        ZZn = PolynomialRing(ZZ, 'n')
        p = self.coeff[i]
        rng = range(p.degree() + 1 - j)
        bin = [ZZ(k+j).binomial(k) for k in rng]
        re = ZZn([bin[k]*p[k+j].real() for k in rng])
        im = ZZn([bin[k]*p[k+j].imag() for k in rng])
        return re, im

    @cached_method
    def scalars_embedding(self, tgt):
        if (utilities.is_QQi(self.Scalars)
                and isinstance(tgt, ComplexBallField)
                and utilities.has_new_ComplexBall_constructor()):
            return True, lambda x, y: ComplexBall(tgt, x, y)
        elif isinstance(self.Scalars, NumberField_absolute):
            # do complicated coercions via QQbar and CLF only once...
            Pol = PolynomialRing(tgt, 'x')
            x = tgt(self.Scalars.gen())
            def emb(elt):
                return Pol([tgt(c) for c in elt._coefficients()])(x)
            return False, emb
        else:
            return False, tgt

    def eval_series(self, tgt, point, ord):
        re_im, mor = self.scalars_embedding(tgt)
        if re_im:
            res = [[None]*ord for _ in self.coeff]
            for i in range(len(self.coeff)):
                for j in range(ord):
                    re, im = self._coeff_series_re_im(i, j)
                    res[i][j] = mor(re(point), im(point))
            return res
        else:
            point = self.Scalars(point)
            return [[mor(self._coeff_series(i,j)(point)) for j in range(ord)]
                for i in range(len(self.coeff))]

    def eval_inv_lc_series(self, point, ord, shift):
        ser = self.base_ring.element_class(
                self.base_ring, # polynomials, viewed as jets
                [self._coeff_series(0, j)(point) for j in range(shift, ord)],
                check=False)
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

class MultDict(dict):

    def __missing__(self, k):
        return 0

class LogSeriesInitialValues(object):
    r"""
    Initial values defining a logarithmic series.

    - ``self.expo`` is an algebraic number representing the “valuation” of the
      log-series,
    - ``self.shift`` is a dictionary mapping an integer shift s to a tuple of
      initial values corresponding to the coefficients of x^s, x^s·log(x), ...,
      x^s·log(x)^k/k! for some k
    """

    def __init__(self, expo, values, dop=None, check=True, mults=None):
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
            all_values = tuple(chain.from_iterable(
                            ini if isinstance(ini, tuple) else (ini,)
                            for ini in values.itervalues()))
        else:
            all_values = values
            values = dict((n, (values[n],)) for n in range(len(values)))
        self.universe = Sequence(all_values).universe()
        if not utilities.is_numeric_parent(self.universe):
            raise ValueError("initial values must coerce into a ball field")

        self.shift = {}
        if mults is not None:
            for s, m in mults:
                self.shift[s] = [self.universe.zero()]*m
        for k, ini in values.iteritems():
            if isinstance(k, tuple): # requires mult != None
                s, m = k
                s = int(s)
                self.shift[s][m] = self.universe(ini)
            else:
                s = int(k)
                self.shift[s] = tuple(self.universe(a) for a in ini)
        self.shift = { s: tuple(ini) for s, ini in self.shift.iteritems() }

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
            for log_power, val in enumerate(ini)
            if ini)

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

    def last_index(self):
        return max(chain(iter((-1,)), (s for s, vals in self.shift.iteritems()
                                        if not all(v.is_zero() for v in vals))))

    @cached_method
    def mult_dict(self):
        return MultDict((s, len(vals)) for s, vals in self.shift.iteritems())

    def compatible(self, others):
        return all(self.mult_dict() == other.mult_dict() for other in others)

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
        if self.dop.leading_coefficient()[0] != 0:
            n = ind.parent().gen()
            self.sl_decomp = [(-n, [(i, 1) for i in range(self.dop.order())])]
        else:
            self.sl_decomp = my_shiftless_decomposition(ind)

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
            self.shifted_bwrec = self.bwrec.shift(self.leftmost)
            self.process_modZ_class()

    def process_modZ_class(self):
        for self.shift, self.mult in reversed(self.shifts):
            self.process_valuation()

    def process_valuation(self):
        for self.log_power in reversed(range(self.mult)):
            self.process_solution()

    def process_solution(self):
        ini = LogSeriesInitialValues(
            dop = self.dop,
            expo = self.leftmost,
            values = { (self.shift, self.log_power): ZZ.one() },
            mults = self.shifts,
            check = False)
        # XXX: inefficient if self.shift >> 0
        value = self.fun(ini)
        sol = FundamentalSolution(
            leftmost = self.leftmost,
            shift = ZZ(self.shift),
            log_power = ZZ(self.log_power),
            value = value)
        self.irred_factor_cols.append(sol)

    def fun(self, ini):
        return None

def exponent_shifts(dop, leftmost):
    bwrec = bw_shift_rec(dop)
    ind = bwrec[0]
    sl_decomp = my_shiftless_decomposition(ind)
    cand = [shifts for fac, shifts in sl_decomp if fac(leftmost).is_zero()]
    assert len(cand) == 1
    shifts = cand[0]
    assert all(s >=0 for s, m in shifts)
    assert shifts[0][0] == 0
    return shifts

def log_series(ini, bwrec, order):
    Coeffs = pushout(bwrec.base_ring.base_ring(), ini.universe)
    log_prec = sum(len(v) for v in ini.shift.itervalues())
    precomp_len = max(1, bwrec.order) # hack for recurrences of order zero
    bwrec_nplus = collections.deque(
            (bwrec.eval_series(Coeffs, i, log_prec)
                for i in range(precomp_len)),
            maxlen=precomp_len)
    series = []
    for n in range(order):
        new_term = vector(Coeffs, log_prec)
        mult = len(ini.shift.get(n, ()))
        for p in range(log_prec - mult - 1, -1, -1):
            combin  = sum(bwrec_nplus[0][i][j]*series[-i][p+j]
                          for j in range(log_prec - p)
                          for i in range(min(bwrec.order, n), 0, -1))
            combin += sum(bwrec_nplus[0][0][j]*new_term[p+j]
                          for j in range(mult + 1, log_prec - p))
            new_term[mult + p] = - ~bwrec_nplus[0][0][mult] * combin
        for p in range(mult - 1, -1, -1):
            new_term[p] = ini.shift[n][p]
        series.append(new_term)
        bwrec_nplus.append(bwrec.eval_series(Coeffs, n+precomp_len, log_prec))
    return series

def log_series_values(Jets, expo, psum, evpt, downshift=[0]):
    r"""
    Evaluate a logarithmic series, and optionally its downshifts.

    That is, compute the vectors (v[0], ..., v[r-1]) such that ::

        Σ[k=0..r] v[k] η^k
            = (pt + η)^expo * Σ_k (psum[d+k]*log(x + η)^k/k!) + O(η^r)

        (x = evpt.pt, r = evpt.jet_order)

    for d ∈ downshift, as an element of ``Jets``, optionally using a
    non-standard branch of the logarithm.

    Note that while this function computes ``pt^expo`` in ℂ, it does NOT
    specialize abstract algebraic numbers that might appear in ``psum``.
    """
    derivatives = evpt.jet_order
    log_prec = psum.length()
    assert all(d < log_prec for d in downshift) or log_prec == 0
    if not evpt.is_numeric:
        if expo != 0 or log_prec > 1:
            raise NotImplementedError("log-series of symbolic point")
        return [vector(psum[0][i] for i in range(derivatives))]
    pt = Jets.base_ring()(evpt.pt)
    if log_prec > 1 or expo not in ZZ or evpt.branch != (0,):
        pt = pt.parent().complex_field()(pt)
        Jets = Jets.change_ring(Jets.base_ring().complex_field())
        psum = psum.change_ring(Jets)
    high = Jets([0] + [(-1)**(k+1)*~pt**k/k
                       for k in range(1, derivatives)])
    aux = high*expo
    logger.debug("aux=%s", aux)
    val = [Jets.base_ring().zero() for d in downshift]
    for b in evpt.branch:
        twobpii = pt.parent()(2*b*pi*I)
        # hardcoded series expansions of log(a+η) and (a+η)^λ
        # (too cumbersome to compute directly in Sage at the moment)
        logpt = Jets([pt.log() + twobpii]) + high
        logger.debug("logpt[%s]=%s", b, logpt)
        inipow = ((twobpii*expo).exp()*pt**expo
                *sum(_pow_trunc(aux, k, derivatives)/Integer(k).factorial()
                    for k in range(derivatives)))
        logger.debug("inipow[%s]=%s", b, inipow)
        logterms = [_pow_trunc(logpt, p, derivatives)/Integer(p).factorial()
                    for p in range(log_prec)]
        for d in downshift:
            val[d] += inipow.multiplication_trunc(
                    sum(psum[d+p]._mul_trunc_(logterms[p], derivatives)
                        for p in range(log_prec - d)),
                    derivatives)
    val = [vector(v[i] for i in range(derivatives))/len(evpt.branch)
           for v in val]
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

