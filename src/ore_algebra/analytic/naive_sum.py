# -*- coding: utf-8 - vim: tw=80
"""
Evaluation of convergent D-finite series by direct summation
"""

# TODO:
# - support summing a given number of terms rather than until a target accuracy
# is reached?
# - cythonize critical parts?

import collections, itertools, logging

from sage.matrix.constructor import identity_matrix, matrix
from sage.modules.free_module_element import vector
from sage.rings.complex_arb import ComplexBallField
from sage.rings.infinity import infinity
from sage.rings.integer import Integer
from sage.rings.polynomial import polynomial_element
from sage.rings.polynomial.polynomial_ring_constructor import PolynomialRing
from sage.rings.qqbar import QQbar
from sage.rings.real_arb import RealBallField
from sage.structure.sequence import Sequence

from ore_algebra.ore_algebra import OreAlgebra

from ore_algebra.analytic import accuracy, bounds, utilities
from ore_algebra.analytic.safe_cmp import safe_lt

logger = logging.getLogger(__name__)

################################################################################
# Argument processing etc. (common to the ordinary and the regular case)
################################################################################

# XXX: perhaps introduce a specific object type (with support for exceptional
# indices and RecJets; see also bound_residual_with_logs)
def backward_rec(dop):
    Pols_n = PolynomialRing(dop.base_ring().base_ring(), 'n') # XXX: name
    Rops = OreAlgebra(Pols_n, 'Sn')
    # Using the primitive part here would break the computation of residuals!
    # TODO: add test (arctan); better fix?
    # rop = dop.to_S(Rops).primitive_part().numerator()
    rop = dop.to_S(Rops)
    ordrec = rop.order()
    bwrec = [rop[ordrec-k](Pols_n.gen()-ordrec) for k in xrange(ordrec+1)]
    return bwrec

class EvaluationPoint(object):
    r"""
    A ring element (a complex number, a polynomial indeterminate, perhaps
    someday a matrix) where to evaluate the partial sum of a series, along with
    a “jet order” used to compute derivatives and a bound on the norm of the
    mathematical quantity it represents that can be used to bound the truncation
    error.
    """

    # XXX: choose a single place to set the default value for jet_order
    def __init__(self, pt, rad=None, jet_order=1):
        self.pt = pt
        self.rad = (bounds.IR.coerce(rad) if rad is not None
                    else bounds.IC(pt).above_abs())
        self.jet_order = jet_order

        self.is_numeric = ComplexBallField(2).has_coerce_map_from(pt.parent())
        self.is_real = RealBallField(2).has_coerce_map_from(pt.parent())

    def jet(self, Intervals):
        base_ring = Intervals if self.is_numeric else self.pt.parent()
        Jets = utilities.jets(base_ring, 'eta', self.jet_order)
        return Jets([self.pt, 1])

    def is_precise(self, eps):
        if self.pt.parent().is_exact():
            return True
        elif isinstance(self.pt.parent(), (RealBallField, ComplexBallField)):
            return safe_lt(bounds.IR(self.pt.rad()), eps)

class LogSeriesInitialValues(object):
    r"""
    Initial values defining a logarithmic series.

    - ``expo`` is the algebraic “valuation”,
    - ``shift`` is a dictionary mapping an integer shift s to a tuple of
      initial values corresponding to the coefficients of x^s, x^s·log(x), ...,
      x^s·log(x)^k/k! for some k
    """

    def __init__(self, expo, values):
        self.expo = QQbar.coerce(expo)
        if isinstance(values, dict):
            all_values = sum(values.values(), ()) # concatenation of tuples
        else:
            all_values = values
            values = dict((n, (values[n],)) for n in xrange(len(values)))
        self.universe = Sequence(all_values).universe()
        if not ComplexBallField(2).has_coerce_map_from(self.universe):
            raise ValueError("initial values must coerce into a ball field")
        self.shift = { s: tuple(self.universe(a) for a in ini)
                       for s, ini in values.iteritems() }

        self.is_real = RealBallField(2).has_coerce_map_from(self.universe)

    def is_precise(self, eps):
        if self.universe.is_exact():
            return True
        elif isinstance(self.universe, (RealBallField, ComplexBallField)):
            return all(safe_lt(bounds.IR(x.rad()), eps)
                       for val in self.shift.itervalues()
                       for x in val)
        else:
            return False

def series_sum_ordinary(dop, ini, pt, tgt_error, maj=None,
        stride=50, record_bounds_in=None):
    r"""
    EXAMPLES::

        sage: from sage.rings.real_arb import RealBallField, RBF
        sage: from sage.rings.complex_arb import ComplexBallField, CBF
        sage: QQi.<i> = QuadraticField(-1)

        sage: from ore_algebra.analytic.ui import *
        sage: from ore_algebra.analytic.naive_sum import series_sum_ordinary, EvaluationPoint
        sage: Dops, x, Dx = Diffops()

        sage: dop = ((4*x^2 + 3/58*x - 8)*Dx^10 + (2*x^2 - 2*x)*Dx^9 +
        ....:       (x^2 - 1)*Dx^8 + (6*x^2 - 1/2*x + 4)*Dx^7 +
        ....:       (3/2*x^2 + 2/5*x + 1)*Dx^6 + (-1/6*x^2 + x)*Dx^5 +
        ....:       (-1/5*x^2 + 2*x - 1)*Dx^4 + (8*x^2 + x)*Dx^3 +
        ....:       (-1/5*x^2 + 9/5*x + 5/2)*Dx^2 + (7/30*x - 12)*Dx +
        ....:       8/7*x^2 - x - 2)
        sage: ini = [CBF(-1/16, -2), CBF(-17/2, -1/2), CBF(-1, 1), CBF(5/2, 0),
        ....:       CBF(1, 3/29), CBF(-1/2, -2), CBF(0, 0), CBF(80, -30),
        ....:       CBF(1, -5), CBF(-1/2, 11)]

    Funny: on the following example, both the evaluation point and most of the
    initial values are exact, so that we end up with a significantly better
    approximation than requested::

        sage: series_sum_ordinary(dop, ini, 1/2, RBF(1e-16))
        ([-3.575140703474456...] + [-2.2884877202396862...]*I)

        sage: import logging; logging.basicConfig()
        sage: series_sum_ordinary(dop, ini, 1/2, RBF(1e-30))
        WARNING:ore_algebra.analytic.naive_sum:input intervals too wide for
        requested accuracy
        ...
        ([-3.5751407034...] + [-2.2884877202...]*I)

    In normal usage ``pt`` should be an object coercible into a complex ball.
    Polynomial with ball coefficients are also supported, albeit with some
    restrictions. (This is intended to be used for polynomial indeterminates,
    anything else that works does so more or less by accident.) ::

        sage: from ore_algebra.analytic.accuracy import AbsoluteError
        sage: series_sum_ordinary(Dx - 1, [RBF(1)],
        ....:         EvaluationPoint(x.change_ring(RBF), rad=RBF(1), jet_order=2),
        ....:         AbsoluteError(1e-3), stride=1)
        (... + [0.0083...]*x^5 + [0.0416...]*x^4 + [0.1666...]*x^3
        + 0.5000...*x^2 + x + [1.000 +/- ...e-4],
        ... + [0.0083...]*x^5 + [0.0416...]*x^4 + [0.1666...]*x^3
        + [0.5000...]*x^2 + x + [1.000 +/- ...e-4])

    TESTS::

        sage: b = series_sum_ordinary((x^2 + 1)*Dx^2 + 2*x*Dx, [RBF(0), RBF(1)],
        ....:                         7/10, RBF(1e-30))
        sage: b.parent()
        Vector space of dimension 1 over Real ball field with ... precision
        sage: b[0].rad().exact_rational() < 10^(-30)
        True
        sage: b[0].overlaps(RealBallField(130)(7/10).arctan())
        True

        sage: b = series_sum_ordinary((x^2 + 1)*Dx^2 + 2*x*Dx, [CBF(0), CBF(1)],
        ....:                         (i+1)/2, RBF(1e-30))
        sage: b.parent()
        Vector space of dimension 1 over Complex ball field with ... precision
        sage: b[0].overlaps(ComplexBallField(130)((1+i)/2).arctan())
        True
    """

    # The code that depends neither on the numeric precision nor on the
    # ordinary/regsing dichotomy goes here.

    if not isinstance(ini, LogSeriesInitialValues):
        ini = LogSeriesInitialValues(0, ini)

    if not isinstance(pt, EvaluationPoint):
        pt = EvaluationPoint(pt)

    if isinstance(tgt_error, accuracy.RelativeError) and pt.jet_order > 1:
        raise TypeError("relative error not supported when computing derivatives")
    if not isinstance(tgt_error, accuracy.StoppingCriterion):
        input_is_precise = pt.is_precise(tgt_error) and ini.is_precise(tgt_error)
        if not input_is_precise:
            logger.warn("input intervals too wide for requested accuracy")
        tgt_error = accuracy.AbsoluteError(tgt_error, input_is_precise)
    logger.info("target error = %s", tgt_error)

    if maj is None:
        if dop.leading_coefficient().valuation() == 0:
            maj = bounds.bound_diffop(dop)
        else:
            raise TypeError("singular operator, please specify a majorant")
    logger.log(logging.DEBUG-1, "Majorant:\n%s", maj)

    bwrec = backward_rec(dop)
    ivs = (RealBallField if ini.is_real and (pt.is_real or not pt.is_numeric)
           else ComplexBallField)

    # Now do the actual computation, automatically increasing the precision as
    # necessary

    bit_prec = utilities.prec_from_eps(tgt_error.eps)
    bit_prec += 3*bit_prec.nbits()
    while True:
        try:
            psum = series_sum_ordinary_doit(ivs(bit_prec), dop, bwrec, ini, pt,
                    tgt_error, maj, stride, record_bounds_in)
            return psum
        except accuracy.PrecisionError:
            bit_prec *= 2
            logger.info("lost too much precision, restarting with %d bits",
                        bit_prec)

################################################################################
# Ordinary points
################################################################################

def series_sum_ordinary_doit(Intervals, bwrec, ini, pt,
        tgt_error, maj, stride, record_bounds_in):

    if record_bounds_in:
        record_bounds_in[:] = []

    jet = pt.jet(Intervals)
    Jets = jet.parent()
    jetpow = Jets.one()
    radpow = bounds.IR.one()

    ordrec = len(bwrec) - 1
    assert ini.expo.is_zero()
    last = collections.deque([Intervals.zero()]*(ordrec - dop.order() + 1))
    last.extend(Intervals(ini.shift[n][0])
                for n in xrange(dop.order() - 1, -1, -1))
    assert len(last) == ordrec + 1 # not ordrec!

    # Singular part. Not the most natural thing to do here, but hopefully
    # generalizes well to the regular singular case.
    psum = Jets.zero()
    for n in range(dop.order()):
        last.rotate(1)
        term = Jets(last[0])*jetpow
        psum += term
        jetpow *= jet
        radpow *= pt.rad

    tail_bound = bounds.IR(infinity)
    for n in itertools.count(dop.order()):
        last.rotate(1)
        #last[0] = None
        # At this point last[0] should be considered undefined (it will hold
        # the coefficient of z^n later in the loop body) and last[1], ...
        # last[ordrec] are the coefficients of z^(n-1), ..., z^(n-ordrec)
        if n%stride == 0:
            logger.debug("n=%s, sum=%s, last tail_bound=%s",
                         n, psum[0], tail_bound.upper())
            abs_sum = abs(psum[0]) if pt.is_numeric else None
            # last[-1] since last[0] may still be "undefined" and last[1] may
            # not exist in degenerate cases
            if (tgt_error.reached(abs(last[-1])*radpow, abs_sum)
                                or record_bounds_in is not None):
                # Warning: this residual must correspond to the operator stored
                # in maj.dop, which typically isn't the operator
                # series_sum_ordinary was called on (but the result of its
                # conversion via to_T, i.e. its product by a power of x).
                residual = bounds.residual(bwrec, n, list(last)[1:],
                                                       maj.Poly.variable_name())
                tail_bound = maj.matrix_sol_tail_bound(n, pt.rad, [residual],
                                                               ord=pt.jet_order)
                if record_bounds_in is not None:
                    record_bounds_in.append((n, psum, tail_bound))
                if tgt_error.reached(tail_bound, abs_sum):
                    break
        comb = sum(Intervals(bwrec[k](n))*last[k] for k in xrange(1, ordrec+1))
        last[0] = -Intervals(~bwrec[0](n))*comb
        # logger.debug("n = %s, [c(n), c(n-1), ...] = %s", n, list(last))
        term = Jets(last[0])*jetpow
        psum += term
        jetpow *= jet
        radpow *= pt.rad
    # Account for the dropped high-order terms in the intervals we return.
    # - Is this the right place do that?
    # - Overestimation: tail_bound is actually a bound on the Frobenius norm of
    #   the error! (TBI?)
    res = vector(_add_error(x, tail_bound.abs()) for x in psum)
    logger.info("summed %d terms, tail <= %s, coeffwise error <= %s", n,
            tail_bound,
            max(x.rad() for x in res) if pt.is_numeric else "n/a")
    return res

# XXX: drop the 'ring' parameter? pass ctx (→ real/complex?)?
def fundamental_matrix_ordinary(dop, pt, ring, eps, rows, maj):
    eps_col = bounds.IR(eps)/bounds.IR(dop.order()).sqrt()
    evpt = EvaluationPoint(pt, jet_order=rows)
    cols = [
        series_sum_ordinary(dop, ini, evpt, eps_col, maj=maj)
        for ini in identity_matrix(dop.order())]
    return matrix(cols).transpose().change_ring(ring)

################################################################################
# Regular singular points
################################################################################

FundamentalSolution = collections.namedtuple(
    'FundamentalSolution',
    ['valuation', 'log_power', 'value'])

def sort_key_by_asympt(sol):
    return sol.valuation.real(), -sol.log_power, sol.valuation.imag()

# XXX: this should be easier to do!
def my_embeddings(nf):
    alg = nf.gen()
    for emb in nf.complex_embeddings():
        emb_nf = NumberField(nf.polynomial(), nf.variable_name(),
                             embedding=emb(alg))
        yield nf.embeddings(emb_nf)[0]

def my_shiftless_decomposition(pol):
    x = pol.parent().gen()
    fac = pol(-x).shiftless_decomposition()
    return [(sl_class.polynomial()(-x), sl_class.shifts())
            for sl_class, _ in fac]

# TODO: move parts not specific to the naïve summation algo elsewhere
# def fundamental_matrix_regular(dop, pt, ring, eps, rows, maj):
# TODO: test with algebraic valuations
def fundamental_matrix_regular(dop, pt, ring, eps, rows, pplen=0):
    r"""
    TESTS::

        sage: from ore_algebra.analytic.ui import *
        sage: from ore_algebra.analytic.naive_sum import *
        sage: Dops, x, Dx = Diffops()

        sage: fundamental_matrix_regular(x*Dx^2 + (1-x)*Dx, 1, RBF, RBF(1e-10), 2)
        [[1.317902...] 1.000000...]
        [[2.718281...]           0]

        sage: dop = (x+1)*(x^2+1)*Dx^3-(x-1)*(x^2-3)*Dx^2-2*(x^2+2*x-1)*Dx
        sage: fundamental_matrix_regular(dop, 1/3, RBF, RBF(1e-10), 3)
        [ 1.0000000...  [0.321750...]  [0.147723...]]
        [            0  [0.900000...]  [0.991224...]]
        [            0  [-0.2...]      [1.935612...]]

        sage: dop = (
        ....:     (2*x^6 - x^5 - 3*x^4 - x^3 + x^2)*Dx^4
        ....:     + (-2*x^6 + 5*x^5 - 11*x^3 - 6*x^2 + 6*x)*Dx^3
        ....:     + (2*x^6 - 3*x^5 - 6*x^4 + 7*x^3 + 8*x^2 - 6*x + 6)*Dx^2
        ....:     + (-2*x^6 + 3*x^5 + 5*x^4 - 2*x^3 - 9*x^2 + 9*x)*Dx)
        sage: fundamental_matrix_regular(dop, RBF(1/3), RBF, RBF(1e-10), 4, pplen=20)
        [ [3.178847...] [-1.064032...]  [1.000...] [0.3287250...]]
        [ [-8.98193...] [3.2281834...]       [...] [0.9586537...]]
        [ [26.18828...] [-4.063756...]       [...] [-0.123080...]]
        [ [-80.2467...] [9.1907404...]       [...] [-0.119259...]]
    """
    eps_col = bounds.IR(eps)/bounds.IR(dop.order()).sqrt()
    # XXX: switch to precise=True once we can catch PrecisionError's
    col_tgt_error = accuracy.AbsoluteError(eps_col, precise=False)

    # XXX: probably should not use the same domain (ring == Intervals ==
    # Jets.base_ring()) for points and for coefficients
    Jets = utilities.jets(ring, 'eta', rows)
    bwrec = backward_rec(dop)
    ind = bwrec[0]
    n = ind.parent().gen()
    logger.debug("indicial polynomial = %s ~~> %s", ind,
            my_shiftless_decomposition(ind))

    cols = []
    for sl_factor, shifts in my_shiftless_decomposition(ind):
        for irred_factor, irred_mult in sl_factor.factor():
            assert irred_mult == 1
            # Complicated to do here and specialize, for little benefit
            #irred_nf = irred_factor.root_field("leftmost")
            #irred_leftmost = irred_nf.gen()
            #irred_bwrec = [pol(irred_leftmost + n) for pol in bwrec]
            for leftmost, _ in irred_factor.roots(QQbar):
                _, leftmost, _ = leftmost.as_number_field_element()
                emb_bwrec = [pol(leftmost + n) for pol in bwrec]
                maj = bounds.bound_diffop(dop, leftmost, shifts,
                        pol_part_len=pplen, bound_inverse="solve")
                for shift, mult in shifts:
                    for log_power in xrange(mult):
                        logger.info("solution z^(%s+%s)·log(z)^%s/%s! + ···",
                                    leftmost, shift, log_power, log_power)
                        ini = LogSeriesInitialValues(
                            expo = leftmost,
                            values = {s: tuple(ring.one()
                                              if (s, p) == (shift, log_power)
                                              else ring.zero()
                                              for p in xrange(m))
                                      for s, m in shifts})
                        # XXX: inefficient if shift >> 0
                        value = series_sum_regular(ring, dop, emb_bwrec,
                                ini, Jets([pt, 1]), col_tgt_error, maj)
                        sol = FundamentalSolution(
                            valuation = leftmost + shift,
                            log_power = log_power,
                            value = value)
                        # logger.debug("sol=%s\n\n", sol)
                        cols.append(sol)
    cols.sort(key=sort_key_by_asympt)
    return matrix([sol.value for sol in cols]).transpose().change_ring(ring)

def log_series_value(Jets, expo, psum, pt):
    log_prec = psum.length()
    # hardcoded series expansions of log(pt) = log(a+η) and pt^λ = (a+η)^λ (too
    # cumbersome to compute directly in Sage at the moment)
    logpt = Jets([pt.log()] + [(-1)**(k+1)*~pt**k/k for k in xrange(1, log_prec)])
    aux = Jets(logpt[1:]*expo)
    inipow = pt**expo*sum(aux**k/Integer(k).factorial()
                         for k in xrange(log_prec))
    logger.debug("pt=%s, psum=%s, inipow=%s, logpt=%s", pt, psum, inipow, logpt)
    # XXX: why do I need an explicit conversion here?!
    val = inipow*sum(Jets(psum[p])*logpt**p/Integer(p).factorial()
                     for p in xrange(log_prec))
    return val

# This function only handles the case of a “single” series, i.e. a series where
# all indices differ from each other by integers. But since we need logic to go
# past singular indices anyway, we can allow for general initial conditions (at
# roots of the indicial equation belonging to the same shift-equivalence class),
# not just initial conditions associated to canonical solutions.
def series_sum_regular(Intervals, dop, bwrec, ini, pt, tgt_error,
        maj, stride=50):

    orddeq = dop.order()

    Jets = pt.parent()
    derivatives = Jets.modulus().degree()
    ptpow = Jets.one()
    rad = bounds.IC(pt[0]).abs()
    radpow = bounds.IR.one() # XXX: should this be rad^leftmost?

    log_prec = sum(len(v) for v in ini.shift.itervalues())
    ordrec = len(bwrec) - 1
    RecJets = utilities.jets(bwrec[0].base_ring(), 'Sk', log_prec)
    last = collections.deque([vector(Intervals, log_prec)
                              for _ in xrange(ordrec + 1)])
    psum = vector(Jets, log_prec)

    for n in itertools.count():
        last.rotate(1)
        logger.debug("n=%s, sum=%s", n, psum)
        mult = len(ini.shift.get(n, ()))

        # Every few iterations, heuristically check if we have converged and if
        # we still have enough precision. If it looks like the target error may
        # be reached (and unless we're at a “special” index where the stopping
        # criterion may be more complicated), perform a rigorous check.
        cond = (n%stride == 0
            and tgt_error.reached(abs(last[-1][0])*radpow, abs(psum[0][0]))
            and n > orddeq and mult == 0)
        if (cond):
            residual_bound = bounds.bound_residual_with_logs(bwrec, n,
                    list(last)[1:], maj.Poly.variable_name(), log_prec, RecJets)
            # XXX: check that residual_bound (as computed by
            # bound_residual_with_logs) really is what tail_bound expects
            tail_bound = maj.matrix_sol_tail_bound(n, rad, [residual_bound],
                                                            ord=derivatives)
            logger.debug("n=%d, est=%s*%s=%s, res_bnd=%s, tail_bnd=%s",
                    n, abs(last[0][0]), radpow, abs(last[0][0])*radpow,
                    residual_bound, tail_bound)
            if tgt_error.reached(tail_bound):
                break

        n_pert = RecJets([n, 1])
        bwrec_n = [b(n_pert).lift().change_ring(Intervals) for b in bwrec]
        # logger.debug("bwrec_nn=%s", bwrec_n)
        # for i in range(0, ordrec +1):
        #    logger.debug("last[%d]=%s", i, last[i])
        for p in xrange(log_prec - mult - 1, -1, -1):
            combin  = sum(bwrec_n[i][j]*last[i][p+j]
                          for j in xrange(log_prec - p)
                          for i in xrange(ordrec, 0, -1))
            combin += sum(bwrec_n[0][j]*last[0][p+j]
                          for j in xrange(mult + 1, log_prec - p))
            last[0][mult + p] = - ~bwrec_n[0][mult] * combin
        for p in xrange(mult - 1, -1, -1):
            last[0][p] = ini.shift[n][p]
        psum += last[0].change_ring(Jets)*ptpow # suboptimal
        ptpow *= pt
        radpow *= rad

    val = log_series_value(Jets, ini.expo, psum, pt[0])
    # TODO: add_error (before or after singular part?)
    return vector(x for x in val)

################################################################################
# Miscellaneous utilities
################################################################################

# Temporary: later on, polynomials with ball coefficients could implement
# add_error themselves.
def _add_error(approx, error):
    if isinstance(approx, polynomial_element.Polynomial):
        return approx[0].add_error(error) + approx[1:]
    else:
        return approx.add_error(error)

def plot_bounds(dop, ini=None, pt=None, eps=None, pplen=0):
    r"""
    EXAMPLES::

        sage: from sage.rings.real_arb import RBF
        sage: from sage.rings.complex_arb import CBF
        sage: from ore_algebra.analytic.ui import Diffops
        sage: from ore_algebra.analytic import naive_sum
        sage: Dops, x, Dx = Diffops()

        sage: naive_sum.plot_bounds(Dx - 1, [CBF(1)], CBF(i)/2, RBF(1e-20))
        Graphics object consisting of 5 graphics primitives
    """
    import sage.plot.all as plot
    from sage.rings.real_arb import RealBallField, RBF
    from sage.rings.complex_arb import CBF
    from sage.all import VectorSpace, QQ, RIF
    from ore_algebra.analytic.bounds import abs_min_nonzero_root
    if ini is None:
        ini = VectorSpace(QQ, dop.order()).random_element()
    ini = map(RealBallField(400), ini)
    if pt is None:
        lc = dop.leading_coefficient()
        if lc.degree() == 0:
            pt = QQ(2)
        else:
            pt = RIF(abs_min_nonzero_root(lc)/2).simplest_rational()
    if eps is None:
        eps = RBF(1e-50)
    recd = []
    maj = bounds.bound_diffop(dop, pol_part_len=pplen)  # cache in ctx?
    ref_sum = series_sum_ordinary(dop, ini, pt, eps, stride=1,
                                  record_bounds_in=recd, maj=maj)
    # Note: this won't work well when the errors get close to the double
    # precision underflow threshold.
    error_plot_upper = plot.line(
            [(n, (psum[0]-ref_sum[0]).abs().upper())
             for n, psum, _ in recd],
            color="lightgray", scale="semilogy")
    error_plot = plot.line(
            [(n, (psum[0]-ref_sum[0]).abs().lower())
             for n, psum, _ in recd],
            color="black", scale="semilogy")
    bound_plot_lower = plot.line([(n, bound.lower()) for n, _, bound in recd],
                           color="lightblue", scale="semilogy")
    bound_plot = plot.line([(n, bound.upper()) for n, _, bound in recd],
                           color="blue", scale="semilogy")
    title = repr(dop) + " @ x=" + repr(pt)
    title = title if len(title) < 80 else title[:77]+"..."
    myplot = error_plot_upper + error_plot + bound_plot_lower + bound_plot
    ymax = myplot.ymax()
    if ymax < float('inf'):
        txt = plot.text(title, (myplot.xmax(), ymax),
                        horizontal_alignment='right', vertical_alignment='top')
        myplot += txt
    return myplot

