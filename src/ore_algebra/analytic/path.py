# -*- coding: utf-8 - vim: tw=80
"""
Analytic continuation paths
"""

# Copyright 2015, 2016, 2017, 2018, 2019 Marc Mezzarobba
# Copyright 2015, 2016, 2017, 2018, 2019 Centre national de la recherche scientifique
# Copyright 2015, 2016, 2017, 2018 Université Pierre et Marie Curie
# Copyright 2019 Sorbonne Université
#
# Distributed under the terms of the GNU General Public License (GPL) either
# version 2, or (at your option) any later version
#
# http://www.gnu.org/licenses/

import logging, sys

import sage.plot.all as plot
import sage.rings.all as rings
import sage.rings.number_field.number_field as number_field
import sage.rings.number_field.number_field_base as number_field_base
import sage.structure.coerce
import sage.symbolic.ring

from sage.misc.cachefunc import cached_method
from sage.rings.all import ZZ, QQ, CC, RIF, CIF, QQbar, RLF, CLF, Integer
from sage.rings.complex_arb import CBF, ComplexBallField, ComplexBall
from sage.rings.real_arb import RBF, RealBallField, RealBall
from sage.structure.sage_object import SageObject

from .accuracy import IR, IC
from .differential_operator import DifferentialOperator
from .local_solutions import (FundamentalSolution, sort_key_by_asympt,
        LocalBasisMapper)
from .safe_cmp import *
from .utilities import *

logger = logging.getLogger(__name__)

QQi = number_field.QuadraticField(-1, 'i')

class PathPrecisionError(Exception):
    pass

######################################################################
# Points
######################################################################

class Point(SageObject):
    r"""
    A point on the complex plane with an associated differential operator.

    A point can be exact (a number field element) or inexact (a real or complex
    interval or ball). It can be classified as ordinary, regular singular, etc.
    The main reason for making the operator part of the definition of Points is
    that this gives a convenient place to cache information that depend on both,
    with an appropriate lifetime. Note however that the point is considered to
    lie on the complex plane, not on the Riemann surface of the operator.
    """

    def __init__(self, point, dop=None, singular=None, **kwds):
        """
        INPUT:

        - ``singular``: can be set to True to force this point to be considered
          a singular point, even if this cannot be checked (e.g. because we only
          have an enclosure)

        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point
            sage: Dops, x, Dx = DifferentialOperators()
            sage: [Point(z, Dx)
            ....:  for z in [1, 1/2, 1+I, QQbar(I), RIF(1/3), CIF(1/3), pi,
            ....:  RDF(1), CDF(I), 0.5r, 0.5jr, 10r, QQbar(1), AA(1/3)]]
            [1, 1/2, I + 1, I, [0.333333333333333...], [0.333333333333333...],
            3.141592653589794?, ~1.0000, ~1.0000*I, ~0.50000, ~0.50000*I, 10,
            1, 1/3]
            sage: Point(sqrt(2), Dx).iv()
            [1.414...]
            sage: Point(RBF(0), (x-1)*x*Dx, singular=True).dist_to_sing()
            1.000000000000000
        """
        SageObject.__init__(self)

        from sage.rings.complex_double import ComplexDoubleField_class
        from sage.rings.complex_field import ComplexField_class
        from sage.rings.complex_interval_field import ComplexIntervalField_class
        from sage.rings.real_double import RealDoubleField_class
        from sage.rings.real_mpfi import RealIntervalField_class
        from sage.rings.real_mpfr import RealField_class

        point = sage.structure.coerce.py_scalar_to_element(point)
        try:
            parent = point.parent()
        except AttributeError:
            raise TypeError("unexpected value for point: " + repr(point))
        if isinstance(point, Point):
            self.value = point.value
        elif isinstance(parent, (
                number_field_base.NumberField,
                RealBallField, ComplexBallField)):
            self.value = point
        elif QQ.has_coerce_map_from(parent):
            self.value = QQ.coerce(point)
        elif QQbar.has_coerce_map_from(parent):
            alg = QQbar.coerce(point)
            NF, val, hom = alg.as_number_field_element()
            if NF is QQ:
                self.value = QQ.coerce(val) # parent may be ZZ
            else:
                embNF = number_field.NumberField(NF.polynomial(),
                                                NF.variable_name(),
                                                embedding=hom(NF.gen()))
                self.value = val.polynomial()(embNF.gen())
        elif isinstance(parent, (RealField_class, RealDoubleField_class,
                                 RealIntervalField_class)):
            self.value = RealBallField(point.prec())(point)
        elif isinstance(parent, (ComplexField_class, ComplexDoubleField_class,
                                 ComplexIntervalField_class)):
            self.value = ComplexBallField(point.prec())(point)
        elif parent is sage.symbolic.ring.SR:
            try:
                return self.__init__(point.pyobject(), dop)
            except TypeError:
                pass
            try:
                return self.__init__(QQbar(point), dop)
            except (TypeError, ValueError, NotImplementedError):
                pass
            try:
                self.value = RLF(point)
            except (TypeError, ValueError):
                self.value = CLF(point)
        else:
            try:
                self.value = RLF.coerce(point)
            except TypeError:
                self.value = CLF.coerce(point)

        parent = self.value.parent()
        assert (isinstance(parent, (number_field_base.NumberField,
                                    RealBallField, ComplexBallField))
                or parent is RLF or parent is CLF)

        if dop is None: # TBI
            if isinstance(point, Point):
                self.dop = point.dop
        else:
            self.dop = DifferentialOperator(dop.numerator())
        self._force_singular = bool(singular)
        self.options = kwds

    def _repr_(self, size=False):
        """
        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point
            sage: Dops, x, Dx = DifferentialOperators()
            sage: Point(10**20, Dx)
            ~1.0000e20
        """
        if self.is_exact():
            try:
                len = (self.value.parent().precision()
                        if isinstance(self.value, (RealBall, ComplexBall))
                        else self.nbits())
                if len > 50:
                    res = repr(self.value.n(digits=5))
                    if size:
                        return "~[{}b]{}".format(self.nbits(), res)
                    else:
                        return "~" + res
            except AttributeError:
                pass
        return repr(self.value)

    def keep_value(self):
        return bool(self.options.get("keep_value"))

    def nbits(self):
        if isinstance(self.value, (RealBall, ComplexBall)):
            return self.value.nbits()
        else:
            res = self.value.denominator().nbits()
            res += max(self.value.numerator().real().numerator().nbits(),
                        self.value.numerator().imag().numerator().nbits())
            return res

    def bit_burst_bits(self, tgt_prec):
        parent = self.value.parent()
        if isinstance(self.value, (RealBall, ComplexBall, rings.Integer,
                                             rings.Rational)) or is_QQi(parent):
            return self.nbits()
        else:
            # RLF, CLF, other number fields (debatable!)
            return tgt_prec

    # Numeric representations

    @cached_method
    def iv(self):
        """
        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.path import Point
        sage: Dops, x, Dx = DifferentialOperators()
        sage: [Point(z, Dx).iv()
        ....: for z in [1, 1/2, 1+I, QQbar(I), RIF(1/3), CIF(1/3), pi]]
        [1.000000000000000,
        0.5000000000000000,
        1.000000000000000 + 1.000000000000000*I,
        1.000000000000000*I,
        [0.333333333333333 +/- 3.99e-16],
        [0.333333333333333 +/- 3.99e-16],
        [3.141592653589793 +/- 7.83e-16]]
        """
        return IC(self.value)

    def exact(self):
        r"""
        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.path import Point
        sage: Dops, x, Dx = DifferentialOperators()
        sage: QQi.<i> = QuadraticField(-1)
        sage: [Point(z, Dx).exact() for z in [1, 1/2, 1+i, QQbar(I)]]
        [1, 1/2, i + 1, I]
        sage: [Point(z, Dx).exact() for z in [RBF(3/4), RBF(1) + I]]
        [3/4, i + 1]
        sage: Point(RIF(1/3), Dx).exact()
        Traceback (most recent call last):
        ...
        ValueError
        """
        if self.value.parent().is_exact():
            return self
        elif isinstance(self.value, RealBall) and self.value.is_exact():
            return Point(QQ(self.value), self.dop, **self.options)
        elif isinstance(self.value, ComplexBall) and self.value.is_exact():
            value = QQi((QQ(self.value.real()), QQ(self.value.imag())))
            return Point(value, self.dop, **self.options)
        raise ValueError

    def approx_abs_real(self, prec):
        r"""
        Compute an approximation with absolute error about 2^(-prec).
        """
        if isinstance(self.value.parent(), RealBallField):
            return self.value
        elif self.value.is_zero():
            return RealBallField(max(2, prec)).zero()
        elif self.is_real():
            expo = ZZ(IR(self.value).abs().log(2).upper().ceil())
            rel_prec = max(2, prec + expo + 10)
            val = RealBallField(rel_prec)(self.value)
            return val
        else:
            raise ValueError("point may not be real")

    def is_real(self):
        return is_real_parent(self.value.parent())

    def is_exact(self):
        r"""
        Is this point exact in the sense that we can use it in the coefficients
        of an operator?
        """
        return (isinstance(self.value, (rings.Integer, rings.Rational,
                                        rings.NumberFieldElement))
                or isinstance(self.value, (RealBall, ComplexBall))
                    and self.value.is_exact())

    def rationalize(self):
        a = self.iv()
        if any(a.overlaps(s) for s in self.dop._singularities(IC)):
            raise PathPrecisionError
        else:
            return Point(_rationalize(a), self.dop)

    def truncate(self, prec, tgt_prec):
        Ivs = RealBallField if self.is_real() else ComplexBallField
        approx = Ivs(prec)(self.value).round()
        lc = self.dop.leading_coefficient()
        if lc(approx).contains_zero():
            raise PathPrecisionError # appropriate?
        approx = approx.squash()
        return Point(Ivs(tgt_prec)(approx), self.dop)

    # Point equality is identity

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)

    def __lt__(self, other):
        r"""
        Temporary kludge (Sage graphs require vertices to be comparable).
        """
        return id(self) < id(other)

    ### Methods that depend on dop

    @cached_method
    def is_ordinary(self):
        if self._force_singular:
            return False
        lc = self.dop.leading_coefficient()
        if not lc(self.iv()).contains_zero():
            return True
        if self.is_exact():
            try:
                val = lc(self.value)
            except TypeError: # work around coercion weaknesses
                val = lc.change_ring(QQbar)(QQbar.coerce(self.value))
            return not val.is_zero()
        else:
            raise ValueError("can't tell if inexact point is singular")

    def is_singular(self):
        return not self.is_ordinary()

    @cached_method
    def is_regular(self):
        try:
            if self.is_ordinary():
                return True
        except ValueError:
            # we could handle balls containing no irregular singular point...
            raise NotImplementedError("can't tell if inexact point is regular")
        assert self.is_exact()
        # Fuchs criterion
        dop, pt = self.dop.extend_scalars(self.value)
        Pols = dop.base_ring()
        lin = Pols([pt, -1])
        ref = dop.leading_coefficient().valuation(lin) - dop.order()
        return all(coef.valuation(lin) - k >= ref for k, coef in enumerate(dop))

    def is_regular_singular(self):
        return not self.is_ordinary() and self.is_regular()

    def is_irregular(self):
        return not is_regular(self)

    def singularity_type(self, short=False):
        r"""
        EXAMPLES::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point
            sage: Dops, x, Dx = DifferentialOperators()

            sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx
            sage: Point(1, dop).singularity_type()
            'ordinary point'
            sage: Point(i, dop).singularity_type()
            'regular singular point'
            sage: Point(0, x^2*Dx + 1).singularity_type()
            'irregular singular point'
            sage: Point(CIF(1/3), x^2*Dx + 1).singularity_type()
            'ordinary point'
            sage: Point(CIF(1/3)-1/3, x^2*Dx + 1).singularity_type()
            'point of unknown singularity type'
        """
        try:
            if self.is_ordinary():
                return "" if short else "ordinary point"
            elif self.is_regular():
                return "regular singular point"
            else:
                return "irregular singular point"
        except (ValueError, NotImplementedError):
            return "point of unknown singularity type"

    def descr(self):
        t = self.singularity_type(short=True)
        if t == "":
            return repr(self)
        else:
            return t + " " + repr(self)

    def dist_to_sing(self):
        """
        Distance of self to the singularities of self.dop *other than self*.

        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point
            sage: Dops, x, Dx = DifferentialOperators()
            sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx
            sage: Point(1, dop).dist_to_sing()
            [1.41421356237309...]
            sage: Point(i, dop).dist_to_sing()
            2.00...
            sage: Point(1+i, dop).dist_to_sing()
            1.00...

        """
        sing = self.dop._singularities(IC)
        close, distant = split(lambda s: s.overlaps(self.iv()), sing)
        if (len(close) >= 2 or len(close) == 1 and not self.is_singular()):
            raise NotImplementedError # refine?
        dist = [(self.iv() - s).abs() for s in distant]
        min_dist = IR(rings.infinity).min(*dist)
        if min_dist.contains_zero():
            raise NotImplementedError # refine???
        return IR(min_dist.lower())

    def local_basis_structure(self):
        r"""
        EXAMPLES::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point
            sage: Dops, x, Dx = DifferentialOperators()
            sage: Point(0, x*Dx^2 + Dx + x).local_basis_structure()
            [FundamentalSolution(leftmost=0, shift=0, log_power=1, value=None),
             FundamentalSolution(leftmost=0, shift=0, log_power=0, value=None)]
            sage: Point(0, Dx^3 + x*Dx + x).local_basis_structure()
            [FundamentalSolution(leftmost=0, shift=0, log_power=0, value=None),
             FundamentalSolution(leftmost=0, shift=1, log_power=0, value=None),
             FundamentalSolution(leftmost=0, shift=2, log_power=0, value=None)]
        """
        # TODO: provide a way to compute the first terms of the series. First
        # need a good way to share code with fundamental_matrix_regular. Or
        # perhaps modify generalized_series_solutions() to agree with our
        # definition of the basis?
        if self.is_ordinary(): # support inexact points in this case
            return [FundamentalSolution(QQbar.zero(), ZZ(expo), ZZ.zero(), None)
                    for expo in range(self.dop.order())]
        elif not self.is_regular():
            raise NotImplementedError("irregular singular point")
        return LocalBasisMapper(self.dop.shift(self)).run()

    @cached_method
    def simple_approx(self, alg=True):
        # Point options become meaningless (and are lost) when not returning
        # self.
        if isinstance(self.value, (RealBall, ComplexBall)):
            # XXX In the binary splitting regime, we should use a low-precision
            # approximation or a rational approx as in the other branch.
            if self.value.is_exact():
                return self
            else:
                return Point(self.value.squash(), self.dop)
        # XXX: Should ideally be integrated with the bit-burst method when
        # relevant in order to get better paths.
        elif (self.value.parent() in (RLF, CLF)
                or isinstance(self.value, rings.NumberFieldElement)
                and self.value.parent().degree() > 2 and self.is_ordinary()):
            ball = self.iv().add_error(self.dist_to_sing()/16)
            if any(s.overlaps(ball) for s in self.dop._singularities(IC)):
                return self
            rat = _rationalize(ball, real=self.is_real())
            return Point(rat, self.dop)
        return self

class EvaluationPoint(object):
    r"""
    Series evaluation point/jet.

    A ring element (a complex number, a polynomial indeterminate, perhaps
    someday a matrix) where to evaluate the partial sum of a series, along with
    a “jet order” used to compute derivatives and a bound on the norm of the
    mathematical quantity it represents that can be used to bound the truncation
    error.

    * ``branch`` - branch of the logarithm to use; ``(0,)`` means the standard
      branch, ``(k,)`` means log(z) + 2kπi, a tuple of length > 1 averages over
      the corresponding branches
    """

    # XXX: choose a single place to set the default value for jet_order
    def __init__(self, pt, jet_order=1, branch=(0,), rad=None ):
        self.pt = pt
        self.rad = (IR.coerce(rad) if rad is not None
                    else IC(pt).above_abs())
        self.jet_order = jet_order
        self.branch=branch

        self.is_numeric = is_numeric_parent(pt.parent())

    def __repr__(self):
        fmt = "{} + η + O(η^{}) (with |.| ≤ {})"
        return fmt.format(self.pt, self.jet_order + 1, self.rad)

    def jet(self, Intervals):
        base_ring = (Intervals if self.is_numeric
                     else mypushout(self.pt.parent(), Intervals))
        Pol = PolynomialRing(base_ring, 'delta')
        return Pol([self.pt, 1]).truncate(self.jet_order)

    def is_real(self):
        return is_real_parent(self.pt.parent())

    def is_real_or_symbolic(self):
        return self.is_real() or not self.is_numeric

    def accuracy(self):
        if self.pt.parent().is_exact():
            return IR.maximal_accuracy()
        elif isinstance(self.pt.parent(), (RealBallField, ComplexBallField)):
            return self.pt.accuracy()
        else:
            raise ValueError

######################################################################
# Paths
######################################################################

class Step(SageObject):
    r"""
    Analytic continuation step from a :class:`Point` to another

    EXAMPLES::

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.path import Point, Step
        sage: QQi.<i> = QuadraticField(-1)
        sage: Dops, x, Dx = DifferentialOperators()

        sage: s1 = Step(Point(0, x*Dx-1), Point(i/7, x*Dx-1))
        sage: s2 = Step(Point(RIF(1/3), x*Dx-1), Point(pi, x*Dx-1))
        sage: s3 = Step(Point(-i, x*Dx-1), Point(i, x*Dx-1))

        sage: s1, s2
        (0 --> 1/7*i, [0.333333333333333 +/- 3.99e-16] --> 3.141592653589794?)

        sage: list(s1), list(s2)
        ([0, 1/7*i], [[0.333333333333333 +/- 3.99e-16], 3.141592653589794?])

        sage: s1.is_exact(), s2.is_exact()
        (True, False)

        sage: s1.delta(), s2.delta()
        (1/7*i, [2.80825932025646 +/- 1.56e-15])

        sage: s1.length(), s2.length()
        ([0.142857142857142...], [2.8082593202564...])

        sage: s1.check_singularity()
        sage: s2.check_singularity()
        sage: s3.check_singularity()
        Traceback (most recent call last):
        ...
        ValueError: Step -i --> i passes through or too close to singular point
        0 (to compute the connection to a singular point, make it a vertex of
        the path)

        sage: s2.check_convergence()
        Traceback (most recent call last):
        ...
        ValueError: Step ... escapes from the disk of (guaranteed)
        convergence of the solutions at ...

        sage: s2.plot()
        Graphics object consisting of 1 graphics primitive

    TESTS:

    Check that we can handle connections between points in ℚ[i] and in other
    complex number fields in spite of various weaknesses of the coercion system.
    Thanks to Armin Straub for the example::

        sage: dop = ((81*x^4 + 14*x^3 + x^2)*Dx^3 + (486*x^3 + 63*x^2 +
        ....: 3*x)*Dx^2 + (567*x^2 + 48*x + 1)*Dx + 81*x + 3)
        sage: dop.numerical_transition_matrix([0,QQbar((4*sqrt(2)*I-7)/81)])[0,0]
        [-3.17249673357...] + [-4.486587907205...]*I
    """

    def __init__(self, start, end, type=None, branch=None, max_split=None):
        if not (isinstance(start, Point) and isinstance(end, Point)):
            raise TypeError
        if start.dop != end.dop:
            raise ValueError
        self.start = start
        self.end = end
        self.branch = (0,) if branch is None else branch
        self.type = type
        self.max_split = 3 if max_split is None else max_split

    def _repr_(self):
        type = "" if self.type is None else "[{}] ".format(self.type)
        bb = (self.type == "bit-burst")
        start = self.start._repr_(size=bb)
        end = self.end._repr_(size=bb)
        return type + start + " --> " + end

    def __getitem__(self, i):
        if i == 0:
            return self.start
        elif i == 1:
            return self.end
        else:
            raise IndexError

    def is_exact(self):
        return self.start.is_exact() and self.end.is_exact()

    def delta(self):
        r"""
        TESTS::

            sage: from ore_algebra import *
            sage: Dops, x, Dx = DifferentialOperators()
            sage: (Dx - 1).numerical_solution([1], [0, RealField(10)(.33), 1])
            [2.71828182845904...]
        """
        z0, z1 = self.start.value, self.end.value
        if z0.parent() is z1.parent():
            return z1 - z0
        elif (isinstance(z0, (RealBall, ComplexBall))
                and isinstance(z1, (RealBall, ComplexBall))):
            p0, p1 = z0.parent().precision(), z1.parent().precision()
            real = isinstance(z0, RealBall) and isinstance(z1, RealBall)
            Tgt = (RealBallField if real else ComplexBallField)(max(p0, p1))
            return Tgt(z1) - Tgt(z0)
        else: # XXX not great when one is in a number field != QQ[i]
            if self.start.is_exact():
                z0 = self.start.exact().value
            if self.end.is_exact():
                z1 = self.end.exact().value
            try:
                d = z1 - z0
            except TypeError:
                # Should be coercions, but embedded number fields currently
                # don't coerce into QQbar...
                d = QQbar(z1) - QQbar(z0)
            # When z0, z1 are number field elements, we want another number
            # field element, not an element of QQbar or AA (even though z1-z0
            # may succeed and return such an element).
            if d.parent() is z0.parent() or d.parent() is z1.parent():
                return d
            else:
                return as_embedded_number_field_element(d)

    def evpt(self, order):
        return EvaluationPoint(self.delta(), order, branch=self.branch)

    def direction(self):
        delta = self.end.iv() - self.start.iv()
        return delta/abs(delta)

    def length(self):
        return IC(self.delta()).abs()

    def prec(self, tgt_prec):
        myIC = ComplexBallField(tgt_prec + 10) # not ideal...
        len = IC(myIC(self.end.value) - myIC(self.start.value)).abs()
        if len.contains_zero():
            return ZZ(sys.maxsize)
        else:
            return -ZZ(len.log(2).upper().ceil())

    def cvg_ratio(self):
        return self.length()/self.start.dist_to_sing()

    def split(self):
        # Ensure that the substeps correspond to convergent series when
        # splitting a singular step
        if self.max_split <= 0:
            raise ValueError
        if self.start.is_singular():
            mid = (self.start.iv() + 2*self.end.iv())/3
        elif self.end.is_singular():
            mid = (2*self.start.iv() + self.end.iv())/3
        else:
            mid = (self.start.iv() + self.end.iv())/2
        mid = Point(mid, self.start.dop)
        mid = mid.rationalize()
        s0 = Step(self.start, mid, type="split", branch=self.branch,
                  max_split=self.max_split-1)
        s1 = Step(mid, self.end, type="split", branch=None,
                  max_split=self.max_split-1)
        return (s0, s1)

    def bit_burst_split(self, tgt_prec, bit_burst_prec):
        z0, z1 = self
        p0, p1 = z0.bit_burst_bits(tgt_prec), z1.bit_burst_bits(tgt_prec)
        if max(p0, p1) <= 2*bit_burst_prec:
            return ()
        elif p0 <= p1:
            z1_tr = z1.truncate(bit_burst_prec, tgt_prec)
            s0 = Step(z0, z1_tr, type="bit-burst",
                      branch=self.branch, max_split=0)
            s1 = Step(z1_tr, z1, type="bit-burst", max_split=0)
        else:
            z0_tr = z0.truncate(bit_burst_prec, tgt_prec)
            s0 = Step(z0, z0_tr, type="bit-burst",
                      branch=self.branch, max_split=0)
            s1 = Step(z0_tr, z1, type="bit-burst", max_split=0)
        return (s0, s1)

    def chain_simple(self, prev):
        assert prev.end is self.start.simple_approx()
        main = Step(prev.end, self.end.simple_approx(), branch=self.branch)
        if self.end.keep_value():
            dev = Step(main.end, self.end, type="deviation", max_split=0)
        else:
            dev = None
        return main, dev

    def singularities(self):
        dop = self.start.dop
        sing = dop._singularities(IC)
        z0, z1 = IC(self.start.value), IC(self.end.value)
        sing = [s for s in sing if s != z0 and s != z1]
        res = []
        for s in sing:
            ds = s - self.start.iv()
            t = (self.end.iv() - self.start.iv())/ds
            if (ds.contains_zero() or t.imag().contains_zero()
                    and not safe_lt(t.real(), IR.one())):
                res.append(s)
        return res

    def check_singularity(self):
        r"""
        Raise an error if this step goes through a singular point or seems to do
        so at our working precision.

        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Point, Step
            sage: Dops, x, Dx = DifferentialOperators(); i = QuadraticField(-1, 'i').gen()
            sage: dop = (x^2 + 1)*Dx
            sage: Step(Point(0, dop), Point(0, dop)).check_singularity()
            sage: Step(Point(0, dop), Point(1, dop)).check_singularity()
            sage: Step(Point(1, dop), Point(1, dop)).check_singularity()
            sage: Step(Point(1, dop), Point(i, dop)).check_singularity()
            sage: Step(Point(i, dop), Point(0, dop)).check_singularity()
            sage: Step(Point(i, dop), Point(i, dop)).check_singularity()
            sage: Step(Point(2*i+1, dop), Point(-11/10, dop)).check_singularity()
            sage: Step(Point(2*i, dop), Point(0, dop)).check_singularity()
            Traceback (most recent call last):
            ...
            ValueError: Step 2*i --> 0 passes through or too close to singular
            point 1*I (to compute the connection to a singular point, make it a
            vertex of the path)
            sage: Step(Point(2*i+1, dop), Point(-1, dop)).check_singularity()
            Traceback (most recent call last):
            ...
            ValueError: Step 2*i + 1 --> -1 passes through or too close to
            singular point 1*I (to compute the connection to a singular point,
            make it a vertex of the path)
        """
        sing = self.singularities()
        if len(sing) > 0:
            plural = "" if len(sing) == 1 else "s"
            sings = ", ".join(str(self.start.dop._sing_as_alg(s)) for s in sing)
            raise ValueError(
                "Step {} passes through or too close to singular point{} {} "
                "(to compute the connection to a singular point, make it "
                "a vertex of the path)".format(self, plural, sings))

    def check_convergence(self):
        r"""
        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import *
            sage: Dops, x, Dx = DifferentialOperators()

            sage: Path([0, 1], x*(x^2+1)*Dx).check_convergence()
            Traceback (most recent call last):
            ...
            ValueError: Step 0 --> 1 escapes from the disk of (guaranteed)
            convergence of the solutions at regular singular point 0

            sage: Path([1, 0], x*(x^2+1)*Dx).check_convergence()
            Traceback (most recent call last):
            ...
            ValueError: Step 1 --> 0 escapes from the disk of (guaranteed)
            convergence of the solutions at regular singular point 0
        """
        ref = self.end if self.end.is_regular_singular() else self.start
        if self.length() >= ref.dist_to_sing(): # not < ?
            raise ValueError("Step {} escapes from the disk of (guaranteed) "
                    "convergence of the solutions at {}"
                    .format(self, ref.descr()))

    def plot(self):
        return plot.arrow2d(self.start.iv().mid(), self.end.iv().mid())

class Path(SageObject):
    """
    A path in ℂ or on the Riemann surface of some operator.

    Note that paths are not the only potentially interesting analytic
    continuation plans: we may reuse already computed transition matrices!

    EXAMPLES::

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.path import Path
        sage: Dops, x, Dx = DifferentialOperators()
        sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx

        sage: path = Path([0, 1+I, CBF(2*I)], dop)
        sage: path
        0 --> I + 1 --> ~2.0000*I
        sage: path[0]
        0 --> I + 1
        sage: path.vert[0]
        0
        sage: len(path)
        2
        sage: path.dop
        (x^2 + 1)*Dx^2 + 2*x*Dx

        sage: path.check_singularity()
        sage: path.check_convergence()
        Traceback (most recent call last):
        ...
        ValueError: Step 0 --> I + 1 escapes from the disk of (guaranteed)
        convergence of the solutions at 0
    """

    def __init__(self, vert, dop):
        r"""
        TESTS::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Path
            sage: Dops, x, Dx = DifferentialOperators()
            sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx

            sage: Path([], Dx)
            Traceback (most recent call last):
            ...
            ValueError: empty path
        """
        SageObject.__init__(self)
        self.dop = dop
        if not vert:
            raise ValueError("empty path")
        self.vert = [v if isinstance(v, Point) else Point(v, dop)
                     for v in vert]

    def __getitem__(self, i):
        r"""
        Return the i-th step of self
        """
        if len(self.vert) < 2:
            raise IndexError
        else:
            return Step(self.vert[i], self.vert[i+1],
                    branch=self.vert[i].options.get("outgoing_branch"))

    def __len__(self):
        return len(self.vert) - 1

    def _repr_(self):
        return " --> ".join(str(v) for v in self.vert)

    def short_repr(self):
        arrow = " --> " if len(self.vert) < 2 else " --> ... --> "
        return repr(self.vert[0]) + arrow + repr(self.vert[-1])

    def plot(self, disks=False):
        gr  = plot.point2d(self.dop._singularities(CC),
                           marker='*', size=200, color='red')
        for step in self:
            gr += step.plot()
        gr.set_aspect_ratio(1)
        if disks:
            for step in self:
                z = step.start.iv().mid()
                gr += plot.circle((z.real(), z.imag()),
                                  step.start.dist_to_sing().lower(),
                                  linestyle='dotted', color='red')
                gr += plot.circle((z.real(), z.imag()),
                                  step.length().lower(),
                                  linestyle='dashed')
        return gr

    def check_singularity(self):
        """
        EXAMPLES::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Path
            sage: Dops, x, Dx = DifferentialOperators()
            sage: QQi.<i> = QuadraticField(-1, 'i')
            sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx

            sage: Path([0], dop).check_singularity()
            sage: Path([1,3], dop).check_singularity()
            sage: Path([0, i], dop).check_singularity()

            sage: Path([42, 1+i/2, -1+3*i/2], dop).check_singularity()
            Traceback (most recent call last):
            ...
            ValueError: Step 1/2*i + 1 --> 3/2*i - 1 passes through or too close
            to singular point 1*I (to compute the connection to a singular
            point, make it a vertex of the path)

        TESTS:

        Check that we detect additional singular points on path segments with
        regular singular endpoints. Adapted from a NumGfun bug found by
        Christoph Koutschan. ::

            sage: dop = (-8*x^3+4*x^4+5*x^2-x)*Dx + 10*x^2-4*x-8*x^3+1
            sage: dop.numerical_transition_matrix([0,1])
            Traceback (most recent call last):
            ...
            ValueError: ...

        Multiple singular points along a single edge::

            sage: (((x-1)*Dx-1)*((x-2)*Dx-2)).numerical_transition_matrix([0,3])
            Traceback (most recent call last):
            ...
            ValueError: Step 0 --> 3 passes through or too close to singular
            points 1, 2...
        """
        for step in self:
            step.check_singularity()

    def check_convergence(self):
        """
        EXAMPLES::

            sage: from ore_algebra import *
            sage: from ore_algebra.analytic.path import Path
            sage: Dops, x, Dx = DifferentialOperators()
            sage: dop = (x^2 + 1)*Dx^2 + 2*x*Dx
            sage: Path([0, 1], dop).check_convergence()
            Traceback (most recent call last):
            ...
            ValueError: Step 0 --> 1 escapes from the disk of (guaranteed)
            convergence of the solutions at 0
            sage: Path([1, 0], dop).check_convergence()
        """
        for step in self:
            step.check_convergence()

    # Path rewriting

    def bypass_singularities(self):
        r"""
        TESTS::

            sage: from ore_algebra import *
            sage: Dops, x, Dx = DifferentialOperators()
            sage: ((x-1)*Dx - 1).numerical_solution([1], [0,2], assume_analytic=True)
            [-1.0000000000000...] + [+/- ...]*I

            sage: dop = ((x - 1)*Dx - 1)*((x - 2)*Dx - 2)
            sage: dop.numerical_solution([1, 0], [0, 3], assume_analytic=True)
            [-3.5000000000000...] + [+/- ...]*I

            sage: QQi.<i> = QuadraticField(-1)
            sage: dop = ((x - i - 1)*Dx - 1)*((x - 2*i - 2)*Dx - 2)
            sage: dop.numerical_solution([1, 0], [0, 3*i + 3], assume_analytic=True)
            [-3.5000000000000...] + [+/- ...]*I
        """
        new = []
        for step in self:
            new.append(step.start)
            dir = step.direction()
            sings = step.singularities()
            for s in sings:
                ds = Point(s, self.dop, singular=True).dist_to_sing()
                d0 = abs(s - step.start.iv())
                d1 = abs(s - step.end.iv())
                zs = []
                if not safe_lt(d0, ds):
                    zs.append(-1)
                zs.append(IC.gen(0))
                if not safe_lt(d1, ds):
                    zs.append(1)
                rad = (ds/2).min(d0, d1)
                new.extend([_rationalize(CIF(s + rad*z*dir)) for z in zs])
        new.append(self.vert[-1])
        new = Path(new, self.dop)
        return new

    def subdivide(self, threshold=IR(0.6), factor=IR(0.5)):
        # TODO:
        # - support paths passing very close to singular points
        new = [self.vert[0]]
        i = 1
        while i < len(self.vert):
            cur, next = new[-1], self.vert[i]
            rad = cur.dist_to_sing()
            dist_to_next = (next.iv() - cur.iv()).abs()
            if (dist_to_next <= threshold*rad if next.is_ordinary()
                else (cur.value == next.value
                      or cur.is_ordinary()
                         and dist_to_next <= threshold*next.dist_to_sing())):
                new.append(next)
                i += 1
            else:
                dir = (next.iv() - cur.iv())/dist_to_next
                interm = cur.iv() + factor*rad*dir
                is_real = interm.imag().is_zero()
                interm = interm.add_error(rad/8)
                Step(cur, Point(interm, self.dop)).check_singularity() # TBI
                interm = _rationalize(interm, is_real)
                new.append(Point(interm, self.dop))
                logger.debug("subdividing %s -> %s", cur, next)
        new = Path(new, self.dop)
        return new

    def find_loops(self): # ???
        raise NotImplementedError

    def optimize_by_homotopy(self):
        raise NotImplementedError

    def bit_burst(self, z0, z1):
        raise NotImplementedError

def local_monodromy_path(sing):
    raise NotImplementedError

def polygon_around(point, size=17):
    # not ideal in the case of a single singularity...
    rad = (point.dist_to_sing()/2).min(1)
    polygon = []
    for k in range(size):
        x = point.iv() + rad*(CBF(2*k)/size).exppii()
        # XXX balls are not supported (or don't work well) as
        # starting/intermediate points
        if not Point(x, point.dop).is_ordinary():
            raise PathPrecisionError
        x = _rationalize(IC(x))
        polygon.append(Point(x, point.dop))
    return polygon

def _rationalize(civ, real=False):
    from sage.rings.real_mpfi import RealIntervalField
    my_RIF = RealIntervalField(civ.real().parent().precision())
    if real or civ.imag().is_zero():
        return my_RIF(civ.real()).simplest_rational()
    else:
        return QQi([my_RIF(civ.real()).simplest_rational(),
                    my_RIF(civ.imag()).simplest_rational()])
