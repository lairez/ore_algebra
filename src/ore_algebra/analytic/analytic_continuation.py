# -*- coding: utf-8 - vim: tw=80
"""
Evaluation of univariate D-finite functions by numerical analytic continuation
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

import logging

import sage.rings.all as rings
import sage.rings.real_arb
import sage.rings.complex_arb

from . import accuracy, bounds, utilities
from . import naive_sum, binary_splitting

from sage.matrix.constructor import identity_matrix, matrix
from sage.rings.complex_arb import ComplexBallField
from sage.rings.integer_ring import ZZ
from sage.rings.number_field.number_field_element import NumberFieldElement
from sage.rings.real_arb import RealBallField
from sage.structure.element import Matrix, canonical_coercion
from sage.structure.sequence import Sequence

from .context import Context, dctx # re-export Context
from .differential_operator import DifferentialOperator
from .path import Path, Step

logger = logging.getLogger(__name__)

def step_transition_matrix(dop, step, eps, rows=None, split=0, ctx=dctx):
    r"""
    TESTS::

        sage: from ore_algebra.examples import fcc
        sage: fcc.dop4.numerical_solution([0, 0, 0, 1], [0, 1], 1e-3)
        [1...] + [+/- ...]*I
    """

    order = dop.order()
    if rows is None:
        rows = order
    z0, z1 = step
    if order == 0:
        logger.debug("%s: trivial case", step)
        return matrix(ZZ) # 0 by 0
    elif z0.value == z1.value:
        logger.debug("%s: trivial case", step)
        return identity_matrix(ZZ, order)[:rows]
    elif z0.is_ordinary() and z1.is_ordinary():
        logger.info("%s: ordinary case", step)
        if z0.is_exact():
            inverse = False
        # XXX maybe also invert the step when z1 is much simpler than z0
        else: # can happen with the very first step
            step = Step(z1, z0, max_split=0)
            inverse = True
    elif z0.is_regular() and z1.is_ordinary():
        logger.info("%s: regular singular case (going out)", step)
        inverse = False
    elif z0.is_ordinary() and z1.is_regular():
        logger.info("%s: regular singular case (going in)", step)
        step = Step(z1, z0)
        inverse = True
        eps /= 2
    else:
        raise ValueError(z0, z1)
    try:
        mat = regular_step_transition_matrix(dop, step, eps, rows,
                fail_fast=(step.max_split > 0), effort=split, ctx=ctx)
    except (accuracy.PrecisionError, bounds.BoundPrecisionError):
        if step.max_split == 0:
            raise # XXX: can we return something?
        logger.info("splitting step...")
        s0, s1 = step.split()
        m0 = step_transition_matrix(dop, s0, eps/4, None, split+1, ctx)
        m1 = step_transition_matrix(dop, s1, eps/4, rows, split+1, ctx)
        mat = m1*m0
    if inverse:
        mat = ~mat
    return mat

def _use_binsplit(dop, step, eps):
    if step.is_exact() and step.branch == (0,):
        # very very crude
        logprec = -eps.log()
        logratio = -step.cvg_ratio().log() # may be nan (entire functions)
        # don't discourage binary splitting too much for very small steps /
        # entire functions
        terms_est = logprec/logratio.min(logprec.log())
        return (terms_est >= 256 + 32*dop.degree()**2)
    else:
        return False

def regular_step_transition_matrix(dop, step, eps, rows, fail_fast, effort,
                                   ctx=dctx):
    ldop = dop.shift(step.start)
    args = (ldop, step.evpt(rows), eps, fail_fast, effort, ctx)
    if ctx.force_binsplit():
        return binary_splitting.fundamental_matrix_regular(*args)
    elif ctx.prefer_binsplit() or _use_binsplit(ldop, step, eps):
        try:
            return binary_splitting.fundamental_matrix_regular(*args)
        except NotImplementedError:
            logger.info("not implemented: falling back on direct summation")
            return naive_sum.fundamental_matrix_regular(*args)
    elif ctx.force_naive() or fail_fast:
        return naive_sum.fundamental_matrix_regular(*args)
    else:
        try:
            return naive_sum.fundamental_matrix_regular(*args)
        except accuracy.PrecisionError as exn:
            try:
                logger.info("not enough precision, trying binary splitting "
                            "as a fallback")
                return binary_splitting.fundamental_matrix_regular(*args)
            except NotImplementedError:
                logger.info("unable to use binary splitting")
                raise exn

def _process_path(dop, path, ctx):

    if not isinstance(path, Path):
        path = Path(path, dop)

    if not ctx.assume_analytic:
        path.check_singularity()
    if not all(x.is_regular() for x in path.vert):
        raise NotImplementedError("analytic continuation through irregular "
                                  "singular points is not supported")

    # FIXME: prevents the reuse of points...
    if ctx.keep == "all":
        for v in path.vert:
            v.options['keep_value'] = True
    elif ctx.keep == "last":
        for v in path.vert:
            v.options['keep_value'] = False
        path.vert[-1].options['keep_value'] = True

    if ctx.assume_analytic:
        path = path.bypass_singularities()
        path.check_singularity()

    path = path.subdivide()
    path.check_singularity()
    path.check_convergence()

    return path

def analytic_continuation(dop, path, eps, ctx=dctx, ini=None, post=None,
                          return_local_bases=False):
    """
    INPUT:

    - ``ini`` (constant matrix, optional) - initial values, one column per
      solution
    - ``post`` (matrix of polynomial/rational functions, optional) - linear
      combinations of the first Taylor coefficients to take, as a function of
      the evaluation point
    - ``return_local_bases`` (boolean) - if True, also compute and return the
      structure of local bases at all points where we are computing values of
      the solution

    OUTPUT:

    A list of dictionaries with information on the computed solution(s) at each
    evaluation point.

    TESTS::

        sage: from ore_algebra import DifferentialOperators
        sage: _, x, Dx = DifferentialOperators()
        sage: (Dx^2 + 2*x*Dx).numerical_solution([0, 2/sqrt(pi)], [0,i])
        [+/- ...] + [1.65042575879754...]*I
    """

    if dop.is_zero():
        raise ValueError("operator must be nonzero")
    _, _, _, dop = dop._normalize_base_ring()

    path = _process_path(dop, path, ctx)
    logger.info("path: %s", path)

    eps = bounds.IR(eps)
    eps1 = (eps/(1 + len(path))) >> 2
    prec = utilities.prec_from_eps(eps1)

    if ini is not None:
        if not isinstance(ini, Matrix): # should this be here?
            try:
                ini = matrix(dop.order(), 1, list(ini))
            except (TypeError, ValueError):
                raise ValueError("incorrect initial values: {}".format(ini))
        try:
            ini = ini.change_ring(RealBallField(prec))
        except (TypeError, ValueError):
            ini = ini.change_ring(ComplexBallField(prec))

    def point_dict(point, value):
        if ini is not None:
            value = value*ini
        if post is not None and not post.is_one():
            value = post(point.value)*value
        rec = {"point": point.value, "value": value}
        if return_local_bases:
            rec["structure"] = point.local_basis_structure()
        return rec

    res = []
    z0 = path.vert[0]
    main = Step(z0, z0.simple_approx())
    path_mat = step_transition_matrix(dop, main, eps1, ctx=ctx)
    if z0.keep_value():
        res.append(point_dict(z0, identity_matrix(ZZ, dop.order())))
    for step in path:
        main, dev = step.chain_simple(main)
        main_mat = step_transition_matrix(dop, main, eps1, ctx=ctx)
        path_mat = main_mat*path_mat
        if dev is not None:
            dev_mat = step_transition_matrix(dop, dev, eps1, ctx=ctx)
            res.append(point_dict(dev.end, dev_mat*path_mat))

    cm = sage.structure.element.get_coercion_model()
    real = (rings.RIF.has_coerce_map_from(dop.base_ring().base_ring())
            and all(v.is_real() for v in path.vert))
    OutputIntervals = cm.common_parent(
            utilities.ball_field(eps, real),
            *[rec["value"].base_ring() for rec in res])
    for rec in res:
        rec["value"] = rec["value"].change_ring(OutputIntervals)
    return res

def normalize_post_transform(dop, post_transform):
    if post_transform is None:
        post_transform = dop.parent().one()
    else:
        _, post_transform = canonical_coercion(dop, post_transform)
    return post_transform % dop
