#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" This module holds the classes for IRGN Optimization without streaming.

Attribues:
  DTYPE (complex64):
    Complex working precission. Currently single precission only.
  DTYPE_real (float32):
    Real working precission. Currently single precission only.
"""
from __future__ import division

import numpy as np
import time
import sys
from pkg_resources import resource_filename
import pyopencl as cl
import pyopencl.array as clarray
import pyqmri.operator as operator
from pyqmri._helper_fun import CLProgram as Program
import h5py

DTYPE = np.complex64
DTYPE_real = np.float32


class ModelReco:
    """ IRGN Optimization

    This Class performs IRGN Optimization either with TGV or TV regularization
    and a combination of TGV/TV + H1.
    """
    def __init__(self, par, trafo=1, imagespace=False, SMS=0):
        self.par = par
        self._fval_old = 0
        self._fval = 0
        self._fval_init = 0
        self._ctx = par["ctx"][0]
        self._queue = par["queue"][0]
        self._ratio = clarray.to_device(
            self._queue,
            (1 /
             self.par["unknowns"] *
             np.ones(
                 self.par["unknowns"])).astype(
                dtype=DTYPE_real))
        self.gn_res = []
        self._prg = Program(
            self._ctx,
            open(
                resource_filename(
                    'pyqmri', 'kernels/OpenCL_Kernels.c')).read())
        if imagespace:
            self._coil_buf = []
            self.op = operator.OperatorImagespace(par, self._prg)
            self.calc_residual = self._calc_residual_imagespace
            self.irgn_solve_3D = self._irgn_solve_3D_imagespace
        else:
            self._coil_buf = cl.Buffer(self._ctx,
                                       cl.mem_flags.READ_ONLY |
                                       cl.mem_flags.COPY_HOST_PTR,
                                       hostbuf=self.par["C"].data)
            self.irgn_solve_3D = self._irgn_solve_3D_kspace
            self.calc_residual = self._calc_residual_kspace
            if SMS:
                self.op = operator.OperatorKspaceSMS(par, self._prg, trafo)
            else:
                self.op = operator.OperatorKspace(par, self._prg, trafo)
            self.FT = self.op.NUFFT.FFT
            self.FTH = self.op.NUFFT.FFTH

    def update_primal(self, x_new, x, Kyk, xk, tau, delta, wait_for=[]):
        return self._prg.update_primal(
            self._queue, x.shape[1:], None, x_new.data, x.data, Kyk.data,
            xk.data, np.float32(tau),
            np.float32(tau / delta), np.float32(1 / (1 + tau / delta)),
            self.min_const.data, self.max_const.data,
            self.real_const.data, np.int32(self.par["unknowns"]),
            wait_for=(x_new.events + x.events +
                      Kyk.events + xk.events + wait_for))

    def update_v(self, v_new, v, Kyk2, tau, wait_for=[]):
        return self._prg.update_v(
            self._queue, (v[..., 0].size,), None, v_new.data, v.data,
            Kyk2.data, np.float32(tau),
            wait_for=v_new.events + v.events + Kyk2.events + wait_for)

    def update_z1(self, z_new, z, gx, gx_, vx, vx_,
                  sigma, theta, alpha, omega, wait_for=[]):
        return self._prg.update_z1(
            self._queue, z.shape[1:-1], None, z_new.data, z.data, gx.data,
            gx_.data, vx.data, vx_.data, np.float32(sigma), np.float32(theta),
            np.float32(1 / alpha), np.int32(self.par["unknowns_TGV"]),
            np.int32(self.par["unknowns_H1"]),
            np.float32(1 / (1 + sigma / omega)),
            wait_for=(
                z_new.events + z.events + gx.events +
                gx_.events + vx.events + vx_.events + wait_for))

    def update_z1_tv(self, z_new, z, gx, gx_,
                     sigma, theta, alpha, omega, wait_for=[]):
        return self._prg.update_z1_tv(
            self._queue, z.shape[1:-1], None, z_new.data, z.data, gx.data,
            gx_.data, np.float32(sigma), np.float32(theta),
            np.float32(1 / alpha), np.int32(self.par["unknowns_TGV"]),
            np.int32(self.par["unknowns_H1"]),
            np.float32(1 / (1 + sigma / omega)),
            wait_for=(
                z_new.events + z.events + gx.events + gx_.events + wait_for))

    def update_z2(self, z_new, z, gx, gx_, sigma, theta, beta, wait_for=[]):
        return self._prg.update_z2(
            self._queue, z.shape[1:-1], None, z_new.data, z.data,
            gx.data, gx_.data, np.float32(sigma), np.float32(theta),
            np.float32(1 / beta), np.int32(self.par["unknowns_TGV"]),
            wait_for=(
                z_new.events + z.events + gx.events + gx_.events + wait_for))

    def update_Kyk2(self, div, u, z, wait_for=[]):
        return self._prg.update_Kyk2(
            self._queue, u.shape[1:-1], None, div.data, u.data, z.data,
            np.int32(self.par["unknowns_TGV"]), np.float32(self.par["dz"]),
            wait_for=div.events + u.events + z.events + wait_for)

    def update_r(self, r_new, r, A, A_, res, sigma, theta, lambd, wait_for=[]):
        return self._prg.update_r(
            self._queue, (r.size,), None, r_new.data, r.data, A.data, A_.data,
            res.data, np.float32(sigma), np.float32(theta),
            np.float32(1 / (1 + sigma / lambd)),
            wait_for=r_new.events + r.events + A.events + A_.events + wait_for)

    def update_primal_explicit(self, x_new, x, Kyk, xk, ATd,
                               tau, delta, lambd, wait_for=[]):
        return self._prg.update_primal_explicit(
            self._queue, x.shape[1:], None, x_new.data, x.data, Kyk.data,
            xk.data, ATd.data, np.float32(tau),
            np.float32(1 / delta), np.float32(lambd), self.min_const.data,
            self.max_const.data,
            self.real_const.data, np.int32(self.par["unknowns"]),
            wait_for=(
                x_new.events + x.events + Kyk.events +
                xk.events + ATd.events + wait_for))

    def _eval_const(self):
        num_const = (len(self.model.constraints))
        self.min_const = np.zeros((num_const), dtype=np.float32)
        self.max_const = np.zeros((num_const), dtype=np.float32)
        self.real_const = np.zeros((num_const), dtype=np.int32)
        for j in range(num_const):
            self.min_const[j] = np.float32(self.model.constraints[j].min)
            self.max_const[j] = np.float32(self.model.constraints[j].max)
            self.real_const[j] = np.int32(self.model.constraints[j].real)
        self.min_const = clarray.to_device(self._queue, self.min_const)
        self.max_const = clarray.to_device(self._queue, self.max_const)
        self.real_const = clarray.to_device(self._queue, self.real_const)

    def _setup_linear_ops(self, TV):
        self.grad_op = operator.OperatorFiniteGradient(self.par, self._prg)
        if TV is not True:
            self.symgrad_op = operator.OperatorFiniteSymGradient(
                self.par, self._prg)

###############################################################################
# Start a 3D Reconstruction, set TV to True to perform TV instead of TGV#######
# Precompute Model and Gradient values for xk #################################
# Call inner optimization #####################################################
# input: bool to switch between TV (1) and TGV (0) regularization #############
# output: optimal value of x ##################################################
###############################################################################
    def _execute_3D(self, TV=0):
        iters = self.irgn_par["start_iters"]

        result = np.copy(self.model.guess)

        self._setup_reg_tmp_arrays(TV)
        self._setup_linear_ops(TV)

        for ign in range(self.irgn_par["max_gn_it"]):
            start = time.time()
            self.grad_x = np.nan_to_num(self.model.execute_gradient(result))
            self._balance_model_gradients(result, ign)
            self.grad_op.updateRatio(result)

            self.step_val = np.nan_to_num(self.model.execute_forward(result))
            self.grad_buf = cl.Buffer(self._ctx,
                                      cl.mem_flags.READ_ONLY |
                                      cl.mem_flags.COPY_HOST_PTR,
                                      hostbuf=self.grad_x.data)

            self._update_reg_par(result, ign)

            result = self.irgn_solve_3D(result, iters, self.data, ign, TV)

            iters = np.fmin(iters * 2, self.irgn_par["max_iters"])

            end = time.time() - start
            self.gn_res.append(self._fval)
            print("-" * 75)
            print("GN-Iter: %d  Elapsed time: %f seconds" % (ign, end))
            print("-" * 75)
            if np.abs(self._fval_old - self._fval) / self._fval_init < \
               self.irgn_par["tol"]:
                print("Terminated at GN-iteration %d because "
                      "the energy decrease was less than %.3e" %
                      (ign,  np.abs(self._fval_old - self._fval) /
                       self._fval_init))
                self.calc_residual(result, self.data, ign+1, TV)
                self.savetofile(ign, self.model.rescale(result), TV)
                break
            self._fval_old = self._fval
            self.savetofile(ign, self.model.rescale(result), TV)
        self.calc_residual(result, self.data, ign+1, TV)

    def _update_reg_par(self, result, ign):
        self.irgn_par["delta_max"] = (self.delta_max /
                                      1e3 * np.linalg.norm(result))
        self.irgn_par["delta"] = np.minimum(
            self.delta /
            (1e3)*np.linalg.norm(result)*self.irgn_par["delta_inc"]**ign,
            self.irgn_par["delta_max"])
        self.irgn_par["gamma"] = np.maximum(
            self.gamma * self.irgn_par["gamma_dec"]**ign,
            self.irgn_par["gamma_min"])
        self.irgn_par["omega"] = np.maximum(
            self.omega * self.irgn_par["omega_dec"]**ign,
            self.irgn_par["omega_min"])

    def _balance_model_gradients(self, result, ind):
        scale = np.reshape(
            self.grad_x,
            (self.par["unknowns"],
             self.par["NScan"] * self.par["NSlice"] *
             self.par["dimY"] * self.par["dimX"]))
        scale = np.linalg.norm(scale, axis=-1)
        print("Initial norm of the model Gradient: \n", scale)
        scale = 1e3 / np.sqrt(self.par["unknowns"]) / scale
        print("Scalefactor of the model Gradient: \n", scale)
        if not np.mod(ind, 1):
            for uk in range(self.par["unknowns"]):
                self.model.constraints[uk].update(scale[uk])
                result[uk, ...] *= self.model.uk_scale[uk]
                self.grad_x[uk] /= self.model.uk_scale[uk]
                self.model.uk_scale[uk] *= scale[uk]
                result[uk, ...] /= self.model.uk_scale[uk]
                self.grad_x[uk] *= self.model.uk_scale[uk]
        scale = np.reshape(
            self.grad_x,
            (self.par["unknowns"],
             self.par["NScan"] * self.par["NSlice"] *
             self.par["dimY"] * self.par["dimX"]))
        scale = np.linalg.norm(scale, axis=-1)
        print("Scale of the model Gradient: \n", scale)

    def _setup_reg_tmp_arrays(self, TV):
        self.r = np.zeros_like(self.data, dtype=DTYPE)
        self.z1 = np.zeros(
            ([self.par["unknowns"], self.par["NSlice"],
              self.par["dimY"], self.par["dimX"], 4]),
            dtype=DTYPE)

        if TV == 1:
            self.tau = np.float32(1 / np.sqrt(8))
            self.beta_line = 400
            self.theta_line = np.float32(1.0)
            pass
        elif TV == 0:
            L = np.float32(0.5 * (18.0 + np.sqrt(33)))
            self.tau = np.float32(1 / np.sqrt(L))
            self.beta_line = 400
            self.theta_line = np.float32(1.0)
            self.v = np.zeros(
                ([self.par["unknowns_TGV"], self.par["NSlice"],
                  self.par["dimY"], self.par["dimX"], 4]),
                dtype=DTYPE)
            self.z2 = np.zeros(
                ([self.par["unknowns_TGV"], self.par["NSlice"],
                  self.par["dimY"], self.par["dimX"], 8]),
                dtype=DTYPE)
        else:
            L = np.float32(0.5 * (18.0 + np.sqrt(33)))
            self.tau = np.float32(1 / np.sqrt(L))
            self.beta_line = 1
            self.theta_line = np.float32(1.0)
            self.v = np.zeros(
                ([self.par["unknowns"], self.par["NSlice"],
                  self.par["dimY"], self.par["dimX"], 4]),
                dtype=DTYPE)
            self.z2 = np.zeros(
                ([self.par["unknowns"], self.par["NSlice"],
                  self.par["dimY"], self.par["dimX"], 8]),
                dtype=DTYPE)

###############################################################################
# New .hdf5 save files ########################################################
###############################################################################
    def savetofile(self, myit, result, TV):
        f = h5py.File(self.par["outdir"]+"output_" + self.par["fname"], "a")
        if not TV:
            f.create_dataset("tgv_result_iter_"+str(myit), result.shape,
                             dtype=DTYPE, data=result)
            f.attrs['res_tgv_iter_'+str(myit)] = self._fval
        else:
            f.create_dataset("tv_result_"+str(myit), result.shape,
                             dtype=DTYPE, data=result)
            f.attrs['res_tv_iter_'+str(myit)] = self._fval
        f.close()

###############################################################################
# Precompute constant terms of the GN linearization step ######################
# input: linearization point x ################################################
# iters: number of innner iterations iters ####################################
# data: the input data ########################################################
# TV: bool to switch between TV (1) and TGV (0) regularization ################
# output: optimal value of x for the inner GN step ############################
###############################################################################
###############################################################################
    def _irgn_solve_3D_kspace(self, x, iters, data, GN_it, TV=0):
        x = clarray.to_device(self._queue, np.require(x, requirements="C"))
        b = clarray.empty(self._queue, data.shape, dtype=DTYPE)
        self.FT(b, clarray.to_device(
            self._queue, self.step_val[:, None, ...] * self.par["C"])).wait()
        res = data - b.get() + self.op.fwdoop(
            [x, self._coil_buf, self.grad_buf]).get()
        x = x.get()
        self._calc_residual_kspace(x, data, GN_it, TV)

        if TV == 1:
            x = self.tv_solve_3D(x, res, iters)
        elif TV == 0:
            x = self.tgv_solve_3D(x, res, iters)
        else:
            x = self.tgv_solve_3D_explicit(x.get(), res, iters)
        return x

    def _calc_residual_kspace(self, x, data, GN_it, TV=0):
        x = clarray.to_device(self._queue, np.require(x, requirements="C"))
        b = clarray.empty(self._queue, data.shape, dtype=DTYPE)
        grad = clarray.to_device(self._queue, np.zeros_like(self.z1))
        grad.add_event(
            self.grad_op.fwd(
                grad,
                x,
                wait_for=grad.events +
                x.events))
        x = x.get()
        self.FT(b, clarray.to_device(
            self._queue,
            (self.step_val[:, None, ...] *
             self.par["C"]))).wait()
        grad = grad.get()
        if TV == 1:
            self._fval = (self.irgn_par["lambd"] / 2 *
                          np.linalg.norm(data - b.get())**2 +
                          self.irgn_par["gamma"] *
                          np.sum(np.abs(grad[:self.par["unknowns_TGV"]])) +
                          self.irgn_par["omega"] / 2 *
                          np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)

        elif TV == 0:
            v = clarray.to_device(self._queue, self.v)
            sym_grad = clarray.to_device(self._queue, np.zeros_like(self.z2))
            sym_grad.add_event(
                self.symgrad_op.fwd(
                    sym_grad,
                    v,
                    wait_for=sym_grad.events +
                    v.events))
            self._fval = (self.irgn_par["lambd"] / 2 *
                          np.linalg.norm(data - b.get())**2 +
                          self.irgn_par["gamma"] *
                          np.sum(np.abs(grad[:self.par["unknowns_TGV"]] -
                                        self.v)) +
                          self.irgn_par["gamma"] * (2) *
                          np.sum(np.abs(sym_grad.get())) +
                          self.irgn_par["omega"] / 2 *
                          np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)
            del sym_grad, v
        else:
            v = clarray.to_device(self._queue, self.v)
            sym_grad = clarray.to_device(self._queue, np.zeros_like(self.z2))
            sym_grad.add_event(
                self.symgrad_op.fwd(
                    sym_grad,
                    v,
                    wait_for=sym_grad.events +
                    v.events))
            self._fval = (self.irgn_par["lambd"] / 2 *
                          np.linalg.norm(data - b.get())**2 +
                          self.irgn_par["gamma"] *
                          np.sum(np.abs(grad[:self.par["unknowns_TGV"]] -
                                        self.v)) +
                          self.irgn_par["gamma"] *
                          2 * np.sum(np.abs(sym_grad.get())) +
                          self.irgn_par["omega"] / 2 *
                          np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)
            del sym_grad, v

        del grad, b
        if GN_it == 0:
            self._fval_init = self._fval
        print("-" * 75)
        print("Function value at GN-Step %i: %f" %
              (GN_it, 1e3*self._fval / self._fval_init))
        print("-" * 75)

###############################################################################
# Precompute constant terms of the GN linearization step ######################
# input: linearization point x ################################################
# iters: number of innner iterations iters ####################################
# data: the input data ########################################################
# TV: bool to switch between TV (1) and TGV (0) regularization ################
# output: optimal value of x for the inner GN step ############################
###############################################################################
###############################################################################
    def _irgn_solve_3D_imagespace(self, x, iters, data, GN_it, TV=0):
        x = clarray.to_device(self._queue, np.require(x, requirements="C"))
        res = data - self.step_val + self.op.fwdoop([x, self.grad_buf]).get()
        x = x.get()

        self._calc_residual_imagespace(x, data, GN_it, TV)

        if TV == 1:
            x = self.tv_solve_3D(x, res, iters)
        elif TV == 0:
            x = self.tgv_solve_3D(x, res, iters)
        return x

    def _calc_residual_imagespace(self, x, data, GN_it, TV=0):
        x = clarray.to_device(self._queue, np.require(x, requirements="C"))
        grad = clarray.to_device(self._queue, np.zeros_like(self.z1))
        grad.add_event(
            self.grad_op.fwd(
                grad,
                x,
                wait_for=grad.events +
                x.events))
        x = x.get()
        grad = grad.get()
        if TV == 1:
            self._fval = (
                self.irgn_par["lambd"] / 2 *
                np.linalg.norm(data - self.step_val)**2 +
                self.irgn_par["gamma"] *
                np.sum(np.abs(grad[:self.par["unknowns_TGV"]])) +
                self.irgn_par["omega"] / 2 *
                np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)
        elif TV == 0:
            v = clarray.to_device(self._queue, self.v)
            sym_grad = clarray.to_device(self._queue, np.zeros_like(self.z2))
            sym_grad.add_event(self.symgrad_op.fwd(
                sym_grad, v, wait_for=sym_grad.events + v.events))
            self._fval = (
                self.irgn_par["lambd"] / 2 *
                np.linalg.norm(data - self.step_val)**2 +
                self.irgn_par["gamma"] *
                np.sum(np.abs(grad[:self.par["unknowns_TGV"]] - self.v)) +
                self.irgn_par["gamma"] * (2) * np.sum(np.abs(sym_grad.get())) +
                self.irgn_par["omega"] / 2 *
                np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)
            del sym_grad, v
        else:
            v = clarray.to_device(self._queue, self.v)
            sym_grad = clarray.to_device(self._queue, np.zeros_like(self.z2))
            sym_grad.add_event(self.symgrad_op.fwd(
                sym_grad, v, wait_for=sym_grad.events + v.events))
            self._fval = (
                self.irgn_par["lambd"] / 2 *
                np.linalg.norm(data - self.step_val)**2 +
                self.irgn_par["gamma"] *
                np.sum(np.abs(grad[:self.par["unknowns_TGV"]] - self.v)) +
                self.irgn_par["gamma"] * (2) * np.sum(np.abs(sym_grad.get())) +
                self.irgn_par["omega"] / 2 *
                np.linalg.norm(grad[self.par["unknowns_TGV"]:])**2)
            del sym_grad, v
        del grad
        if GN_it == 0:
            self._fval_init = self._fval
        print("-" * 75)
        print("Function value at GN-Step %i: %f" %
              (GN_it, 1e3*self._fval / self._fval_init))
        print("-" * 75)

    def tgv_solve_3D(self, x, res, iters):
        alpha = self.irgn_par["gamma"]
        beta = self.irgn_par["gamma"] * 2

        tau = self.tau
        tau_new = np.float32(0)

        x = clarray.to_device(self._queue, x)
        xk = x.copy()
        x_new = clarray.empty_like(x)

        r = clarray.to_device(self._queue, np.zeros_like(self.r))
        r_new = clarray.empty_like(r)
        z1 = clarray.to_device(self._queue, np.zeros_like(self.z1))
        z1_new = clarray.empty_like(z1)
        z2 = clarray.to_device(self._queue, np.zeros_like(self.z2))
        z2_new = clarray.empty_like(z2)
        v = clarray.to_device(self._queue, np.zeros_like(self.v))
        v_new = clarray.empty_like(v)
        res = clarray.to_device(self._queue, res.astype(DTYPE))

        delta = self.irgn_par["delta"]
        omega = self.irgn_par["omega"]
        mu = 1 / delta

        theta_line = self.theta_line
        beta_line = self.beta_line
        beta_new = np.float32(0)
        mu_line = np.float32(0.5)
        delta_line = np.float32(1)
        ynorm = np.float32(0.0)
        lhs = np.float32(0.0)
        primal = np.float32(0.0)
        primal_new = np.float32(0)
        dual = np.float32(0.0)
        gap_init = np.float32(0.0)
        gap_old = np.float32(0.0)
        gap = np.float32(0.0)
        self._eval_const()

        Kyk1 = clarray.empty_like(x)
        Kyk1_new = clarray.empty_like(x)
        Kyk2 = clarray.empty_like(z1)
        Kyk2_new = clarray.empty_like(z1)
        gradx = clarray.empty_like(z1)
        gradx_xold = clarray.empty_like(z1)
        symgrad_v = clarray.empty_like(z2)
        symgrad_v_vold = clarray.empty_like(z2)
        Axold = clarray.empty_like(res)
        Ax = clarray.empty_like(res)

        Axold.add_event(self.op.fwd(Axold, [x, self._coil_buf, self.grad_buf]))
        gradx_xold.add_event(self.grad_op.fwd(gradx_xold, x))
        symgrad_v_vold.add_event(self.symgrad_op.fwd(symgrad_v_vold, v))

        Kyk1.add_event(self.op.adjKyk1(Kyk1,
                                       [r, z1,
                                        self._coil_buf,
                                        self.grad_buf,
                                        self._ratio]))
        Kyk2.add_event(self.update_Kyk2(Kyk2, z2, z1))

        for i in range(iters):
            x_new.add_event(self.update_primal(x_new, x, Kyk1, xk, tau, delta))
            v_new.add_event(self.update_v(v_new, v, Kyk2, tau))

            beta_new = beta_line * (1 + mu * tau)
            tau_new = tau * np.sqrt(beta_line / beta_new * (1 + theta_line))
            beta_line = beta_new

            gradx.add_event(self.grad_op.fwd(gradx, x_new))
            symgrad_v.add_event(self.symgrad_op.fwd(symgrad_v, v_new))
            Ax.add_event(self.op.fwd(Ax,
                                     [x_new, self._coil_buf, self.grad_buf]))

            while True:
                theta_line = tau_new / tau

                z1_new.add_event(self.update_z1(
                    z1_new, z1, gradx, gradx_xold, v_new, v,
                    beta_line * tau_new, theta_line, alpha,
                    omega))
                z2_new.add_event(self.update_z2(
                    z2_new, z2, symgrad_v, symgrad_v_vold,
                    beta_line * tau_new, theta_line, beta))
                r_new.add_event(self.update_r(
                    r_new, r, Ax, Axold, res,
                    beta_line * tau_new, theta_line, self.irgn_par["lambd"]))

                Kyk1_new.add_event(self.op.adjKyk1(Kyk1_new,
                                                   [r_new, z1_new,
                                                    self._coil_buf,
                                                    self.grad_buf,
                                                    self._ratio]))
                Kyk2_new.add_event(self.update_Kyk2(Kyk2_new, z2_new, z1_new))

                ynorm = (
                    (clarray.vdot(r_new - r, r_new - r) +
                     clarray.vdot(z1_new - z1, z1_new - z1) +
                     clarray.vdot(z2_new - z2, z2_new - z2))**(1 / 2)).real
                lhs = np.sqrt(beta_line) * tau_new * (
                    (clarray.vdot(Kyk1_new - Kyk1, Kyk1_new - Kyk1) +
                     clarray.vdot(Kyk2_new - Kyk2, Kyk2_new - Kyk2))**(1 / 2)
                    ).real

                if lhs <= ynorm * delta_line:
                    break
                else:
                    tau_new = tau_new * mu_line

            (Kyk1, Kyk1_new, Kyk2, Kyk2_new, Axold, Ax, z1, z1_new,
             z2, z2_new, r, r_new, gradx_xold, gradx, symgrad_v_vold,
             symgrad_v, tau) = (
             Kyk1_new, Kyk1, Kyk2_new, Kyk2, Ax, Axold, z1_new, z1,
             z2_new, z2, r_new, r, gradx, gradx_xold, symgrad_v,
             symgrad_v_vold, tau_new)

            if not np.mod(i, 50):
                if self.irgn_par["display_iterations"]:
                    self.model.plot_unknowns(x_new.get())
                if self.par["unknowns_H1"] > 0:
                    primal_new = (
                        self.irgn_par["lambd"] / 2 *
                        clarray.vdot(Axold - res, Axold - res) +
                        alpha * clarray.sum(
                            abs((gradx[:self.par["unknowns_TGV"]] - v))) +
                        beta * clarray.sum(abs(symgrad_v)) +
                        1 / (2 * delta) * clarray.vdot(
                            x_new - xk, x_new - xk) +
                        self.irgn_par["omega"] / 2 *
                        clarray.vdot(gradx[self.par["unknowns_TGV"]:],
                                     gradx[self.par["unknowns_TGV"]:])).real

                    dual = (
                        -delta / 2 * clarray.vdot(-Kyk1, -Kyk1)
                        - clarray.vdot(xk, (-Kyk1)) + clarray.sum(Kyk2)
                        - 1 / (2 * self.irgn_par["lambd"]) * clarray.vdot(r, r)
                        - clarray.vdot(res, r)
                        - 1 / (2 * self.irgn_par["omega"])
                        * clarray.vdot(z1[self.par["unknowns_TGV"]:],
                                       z1[self.par["unknowns_TGV"]:])).real
                else:
                    primal_new = (
                        self.irgn_par["lambd"] / 2 *
                        clarray.vdot(Axold - res, Axold - res) +
                        alpha * clarray.sum(
                            abs((gradx - v))) +
                        beta * clarray.sum(abs(symgrad_v)) +
                        1 / (2 * delta) * clarray.vdot(
                            x_new - xk, x_new - xk)).real

                    dual = (
                        -delta / 2 * clarray.vdot(-Kyk1, -Kyk1)
                        - clarray.vdot(xk, (-Kyk1)) + clarray.sum(Kyk2)
                        - 1 / (2 * self.irgn_par["lambd"]) * clarray.vdot(r, r)
                        - clarray.vdot(res, r)).real

                gap = np.abs(primal_new - dual)
                if i == 0:
                    gap_init = gap.get()
                if np.abs(primal - primal_new)/self._fval_init <\
                   self.irgn_par["tol"]:
                    print("Terminated at iteration %d because the energy "
                          "decrease in the primal problem was less than %.3e" %
                          (i,
                           np.abs(primal - primal_new).get()/self._fval_init))
                    self.v = v_new.get()
                    self.r = r.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    return x_new.get()
                if gap > gap_old * self.irgn_par["stag"] and i > 1:
                    self.v = v_new.get()
                    self.r = r.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    print("Terminated at iteration %d "
                          "because the method stagnated" % (i))
                    return x_new.get()
                if np.abs((gap - gap_old) / gap_init) < self.irgn_par["tol"]:
                    self.v = v_new.get()
                    self.r = r.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    print("Terminated at iteration %d because the "
                          "relative energy decrease of the PD gap was "
                          "less than %.3e"
                          % (i, np.abs((gap - gap_old).get() / gap_init)))
                    return x_new.get()
                primal = primal_new
                gap_old = gap
                sys.stdout.write(
                    "Iteration: %04d ---- Primal: %2.2e, "
                    "Dual: %2.2e, Gap: %2.2e \r" %
                    (i, 1000*primal.get() / self._fval_init,
                     1000*dual.get() / self._fval_init,
                     1000*gap.get() / self._fval_init))
                sys.stdout.flush()

            (x, x_new) = (x_new, x)
            (v, v_new) = (v_new, v)

        self.v = v.get()
        self.r = r.get()
        self.z1 = z1.get()
        self.z2 = z2.get()
        return x.get()

    def tgv_solve_3D_explicit(self, x, res, iters):
        alpha = self.irgn_par["gamma"]
        beta = self.irgn_par["gamma"] * 2

        tau = self.tau
        tau_new = np.float32(0)

        self._set_scale(x)
        x = clarray.to_device(self._queue, x)
        xk = x.copy()
        x_new = clarray.empty_like(x)
        ATd = clarray.empty_like(x)

        z1 = clarray.to_device(self._queue, self.z1)
        z1_new = clarray.empty_like(z1)
        z2 = clarray.to_device(self._queue, self.z2)
        z2_new = clarray.empty_like(z2)
        v = clarray.to_device(self._queue, self.v)
        v_new = clarray.empty_like(v)
        res = clarray.to_device(self._queue, res.astype(DTYPE))

        delta = self.irgn_par["delta"]
        omega = self.irgn_par["omega"]
        mu = 1 / delta

        theta_line = self.theta_line
        beta_line = self.beta_line
        beta_new = np.float32(0)
        mu_line = np.float32(0.5)
        delta_line = np.float32(1)
        ynorm = np.float32(0.0)
        lhs = np.float32(0.0)
        primal = np.float32(0.0)
        primal_new = np.float32(0)
        dual = np.float32(0.0)
        gap_min = np.float32(0.0)
        gap = np.float32(0.0)
        self._eval_const()

        Kyk1 = clarray.empty_like(x)
        Kyk1_new = clarray.empty_like(x)
        Kyk2 = clarray.empty_like(z1)
        Kyk2_new = clarray.empty_like(z1)
        gradx = clarray.empty_like(z1)
        gradx_xold = clarray.empty_like(z1)
        symgrad_v = clarray.empty_like(z2)
        symgrad_v_vold = clarray.empty_like(z2)
        AT = clarray.empty_like(res)

        AT.add_event(self.op.fwd(AT, [x, self._coil_buf, self.grad_buf]))
        ATd.add_event(self.op.adj(ATd, [x, self._coil_buf, self.grad_buf]))

        Kyk1.add_event(self.grad_op.adj(Kyk1, z1))
        Kyk2.add_event(self.update_Kyk2(Kyk2, z2, z1))

        for i in range(iters):

            x_new.add_event(
                self.op.adj(AT,
                            [x_new, self._coil_buf, self.grad_buf]))
            x_new.add_event(self.update_primal_explicit(
                x_new, x, Kyk1, xk, ATd,
                tau, delta, self.irgn_par["lambd"]))
            v_new.add_event(self.update_v(v_new, v, Kyk2, tau))

            beta_new = beta_line * (1 + mu * tau)
            tau_new = tau * np.sqrt(beta_line / beta_new * (1 + theta_line))

            beta_line = beta_new

            gradx.add_event(self.grad_op.fwd(gradx, x_new))
            gradx_xold.add_event(self.grad_op.fwd(gradx_xold, x))
            symgrad_v.add_event(self.symgrad_op.fwd(symgrad_v, v_new))
            symgrad_v_vold.add_event(self.symgrad_op.fwd(symgrad_v_vold, v))

            AT.add_event(self.op.fwd(AT,
                                     [x_new, self._coil_buf, self.grad_buf]))

            while True:
                theta_line = tau_new / tau
                z1_new.add_event(self.update_z1(
                    z1_new, z1, gradx, gradx_xold, v_new, v,
                    beta_line * tau_new, theta_line, alpha, omega))
                z2_new.add_event(self.update_z2(
                    z2_new, z2, symgrad_v, symgrad_v_vold,
                    beta_line * tau_new, theta_line, beta))

                Kyk1_new.add_event(self.grad_op.adj(Kyk1_new, z1_new))
                Kyk2_new.add_event(self.update_Kyk2(Kyk2_new, z2_new, z1_new))

                ynorm = ((clarray.vdot(z1_new - z1, z1_new - z1) +
                          clarray.vdot(z2_new - z2, z2_new - z2)
                          )**(1 / 2)).real
                lhs = 1e2 * np.sqrt(beta_line) * tau_new * (
                    (clarray.vdot(Kyk1_new - Kyk1, Kyk1_new - Kyk1) +
                     clarray.vdot(Kyk2_new - Kyk2, Kyk2_new - Kyk2)
                     )**(1 / 2)).real
                if lhs <= ynorm * delta_line:
                    break
                else:
                    tau_new = tau_new * mu_line

            (Kyk1, Kyk1_new, Kyk2, Kyk2_new, z1, z1_new, z2, z2_new, tau) = (
                Kyk1_new, Kyk1, Kyk2_new, Kyk2,
                z1_new, z1, z2_new, z2, tau_new)

            if not np.mod(i, 50):
                self.model.plot_unknowns(x_new.get())
                primal_new = (
                    self.irgn_par["lambd"] / 2 *
                    clarray.vdot(AT - res, AT - res) +
                    alpha * clarray.sum(
                        abs((gradx[:self.par["unknowns_TGV"]] - v))) +
                    beta * clarray.sum(abs(symgrad_v)) +
                    1 / (2 * delta) * clarray.vdot(x_new - xk, x_new - xk)
                    ).real

                dual = (
                    -delta / 2 * clarray.vdot(-Kyk1, -Kyk1)
                    - clarray.vdot(xk, (-Kyk1))
                    + clarray.sum(Kyk2)).real

                gap = np.abs(primal_new - dual)
                if i == 0:
                    gap_min = gap
                if np.abs(primal - primal_new) <\
                   (self.irgn_par["lambd"] * self.par["NSlice"]) * \
                   self.irgn_par["tol"]:
                    print("Terminated at iteration %d because the energy \
                          decrease in the primal problem was less than %.3e" %
                          (i, abs(primal - primal_new).get() /
                           (self.irgn_par["lambd"] * self.par["NSlice"])))
                    self.v = v_new.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    return x_new.get()
                if (gap > gap_min * self.irgn_par["stag"]) and i > 1:
                    self.v = v_new.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    print("Terminated at iteration %d \
                          because the method stagnated" % (i))
                    return x.get()
                if np.abs(gap - gap_min) < \
                   (self.irgn_par["lambd"] * self.par["NSlice"]) * \
                   self.irgn_par["tol"] \
                   and i > 1:
                    self.v = v_new.get()
                    self.z1 = z1.get()
                    self.z2 = z2.get()
                    print("Terminated at iteration %d because the energy \
                          decrease in the PD gap was less than %.3e" %
                          (i, abs(gap - gap_min).get() /
                           (self.irgn_par["lambd"] * self.par["NSlice"])))
                    return x_new.get()
                primal = primal_new
                gap_min = np.minimum(gap, gap_min)
                sys.stdout.write(
                    "Iteration: %d ---- Primal: %f, Dual: %f, Gap: %f \r" %
                    (i,
                     primal.get() / (self.irgn_par["lambd"] *
                                     self.par["NSlice"]),
                     dual.get() / (self.irgn_par["lambd"] *
                                   self.par["NSlice"]),
                     gap.get() / (self.irgn_par["lambd"] *
                                  self.par["NSlice"])))
                sys.stdout.flush()

            (x, x_new) = (x_new, x)
            (v, v_new) = (v_new, v)

        self.v = v.get()
        self.z1 = z1.get()
        self.z2 = z2.get()
        return x.get()

    def tv_solve_3D(self, x, res, iters):
        alpha = self.irgn_par["gamma"]

        tau = self.tau
        tau_new = np.float32(0)

        x = clarray.to_device(self._queue, x)
        xk = x.copy()
        x_new = clarray.empty_like(x)

        r = clarray.to_device(self._queue, np.zeros_like(self.r))
        r_new = clarray.empty_like(r)
        z1 = clarray.to_device(self._queue, np.zeros_like(self.z1))
        z1_new = clarray.empty_like(z1)
        res = clarray.to_device(self._queue, res.astype(DTYPE))

        delta = self.irgn_par["delta"]
        omega = self.irgn_par["omega"]
        mu = 1 / delta

        theta_line = self.theta_line
        beta_line = self.beta_line
        beta_new = np.float32(0)
        mu_line = np.float32(0.5)
        delta_line = np.float32(1)
        ynorm = np.float32(0.0)
        lhs = np.float32(0.0)
        primal = np.float32(0.0)
        primal_new = np.float32(0)
        dual = np.float32(0.0)
        gap_init = np.float32(0.0)
        gap_old = np.float32(0.0)
        gap = np.float32(0.0)
        self._eval_const()

        Kyk1 = clarray.empty_like(x)
        Kyk1_new = clarray.empty_like(x)
        gradx = clarray.empty_like(z1)
        gradx_xold = clarray.empty_like(z1)
        Axold = clarray.empty_like(res)
        Ax = clarray.empty_like(res)

        Axold.add_event(self.op.fwd(Axold, [x, self._coil_buf, self.grad_buf]))
        Kyk1.add_event(self.op.adjKyk1(Kyk1,
                                       [r, z1,
                                        self._coil_buf,
                                        self.grad_buf,
                                        self._ratio]))

        for i in range(iters):
            x_new.add_event(self.update_primal(x_new, x, Kyk1, xk, tau, delta))

            beta_new = beta_line * (1 + mu * tau)
            tau_new = tau * np.sqrt(beta_line / beta_new * (1 + theta_line))
            beta_line = beta_new

            gradx.add_event(self.grad_op.fwd(
                gradx, x_new, wait_for=gradx.events + x_new.events))
            gradx_xold.add_event(self.grad_op.fwd(
                gradx_xold, x, wait_for=gradx_xold.events + x.events))
            Ax.add_event(self.op.fwd(Ax,
                                     [x_new, self._coil_buf, self.grad_buf]))

            while True:
                theta_line = tau_new / tau
                z1_new.add_event(self.update_z1_tv(
                    z1_new, z1, gradx, gradx_xold,
                    beta_line * tau_new, theta_line, alpha, omega))
                r_new.add_event(self.update_r(
                    r_new, r, Ax, Axold, res,
                    beta_line * tau_new, theta_line, self.irgn_par["lambd"]))

                Kyk1_new.add_event(self.op.adjKyk1(Kyk1_new,
                                                   [r_new, z1_new,
                                                    self._coil_buf,
                                                    self.grad_buf,
                                                    self._ratio]))

                ynorm = ((clarray.vdot(r_new - r, r_new - r) +
                          clarray.vdot(z1_new - z1, z1_new - z1))**(1 / 2)
                         ).real
                lhs = np.sqrt(beta_line) * tau_new * (
                    (clarray.vdot(Kyk1_new - Kyk1, Kyk1_new - Kyk1))**(1 / 2)
                    ).real
                if lhs <= ynorm * delta_line:
                    break
                else:
                    tau_new = tau_new * mu_line

            (Kyk1, Kyk1_new,  Axold, Ax, z1, z1_new, r, r_new, gradx_xold,
             gradx, tau) = (
             Kyk1_new, Kyk1,  Ax, Axold, z1_new, z1, r_new, r, gradx,
             gradx_xold, tau_new)

            if not np.mod(i, 50):
                if self.irgn_par["display_iterations"]:
                    self.model.plot_unknowns(x_new.get())
                if self.par["unknowns_H1"] > 0:
                    primal_new = (
                        self.irgn_par["lambd"] / 2 *
                        clarray.vdot(Axold - res, Axold - res) +
                        alpha * clarray.sum(
                            abs((gradx[:self.par["unknowns_TGV"]]))) +
                        1 / (2 * delta) *
                        clarray.vdot(x_new - xk, x_new - xk) +
                        self.irgn_par["omega"] / 2 *
                        clarray.vdot(gradx[self.par["unknowns_TGV"]:],
                                     gradx[self.par["unknowns_TGV"]:])).real
                    dual = (
                        -delta / 2 * clarray.vdot(-Kyk1, -Kyk1)
                        - clarray.vdot(xk, (-Kyk1))
                        - 1 / (2 * self.irgn_par["lambd"]) * clarray.vdot(r, r)
                        - clarray.vdot(res, r)
                        - 1 / (2 * self.irgn_par["omega"])
                        * clarray.vdot(z1[self.par["unknowns_TGV"]:],
                                       z1[self.par["unknowns_TGV"]:])).real
                else:
                    primal_new = (
                        self.irgn_par["lambd"] / 2 *
                        clarray.vdot(Axold - res, Axold - res) +
                        alpha * clarray.sum(
                            abs((gradx[:self.par["unknowns_TGV"]]))) +
                        1 / (2 * delta) * clarray.vdot(x_new - xk, x_new - xk)
                        ).real
                    dual = (
                        -delta / 2 * clarray.vdot(-Kyk1, -Kyk1)
                        - clarray.vdot(xk, (-Kyk1))
                        - 1 / (2 * self.irgn_par["lambd"]) * clarray.vdot(r, r)
                        - clarray.vdot(res, r)).real
                gap = np.abs(primal_new - dual)

                if i == 0:
                    gap_init = gap
                if np.abs(primal - primal_new)/self._fval_init < \
                   self.irgn_par["tol"]:
                    print("Terminated at iteration %d because the energy "
                          "decrease in the primal problem was less than %.3e" %
                          (i, np.abs(primal - primal_new).get() /
                              self._fval_init))
                    self.r = r.get()
                    self.z1 = z1.get()
                    return x_new.get()
                if (gap > gap_old * self.irgn_par["stag"]) and i > 1:
                    self.r = r.get()
                    self.z1 = z1.get()
                    print("Terminated at iteration %d "
                          "because the method stagnated" % (i))
                    return x_new.get()
                if np.abs((gap - gap_old) / gap_init) < self.irgn_par["tol"]:
                    self.r = r.get()
                    self.z1 = z1.get()
                    print("Terminated at iteration %d because the relative "
                          "energy decrease of the PD gap was less than %.3e" %
                          (i, np.abs((gap - gap_old).get() / gap_init)))
                    return x_new.get()
                primal = primal_new
                gap_old = gap
                sys.stdout.write(
                    "Iteration: %04d ---- Primal: %2.2e, "
                    "Dual: %2.2e, Gap: %2.2e \r" %
                    (i, 1000 * primal.get() / self._fval_init,
                     1000 * dual.get() / self._fval_init,
                     1000 * gap.get() / self._fval_init))
                sys.stdout.flush()

            (x, x_new) = (x_new, x)

        self.r = r.get()
        self.z1 = z1.get()
        return x.get()

    def execute(self, TV=0, imagespace=0, reco_2D=0):
        if reco_2D:
            print("2D currently not implemented, \
                  3D can be used with a single slice.")
            raise NotImplementedError
        else:
            self.irgn_par["lambd"] *= self.par["SNR_est"]
            self.delta = self.irgn_par["delta"]
            self.delta_max = self.irgn_par["delta_max"]
            self.gamma = self.irgn_par["gamma"]
            self.omega = self.irgn_par["omega"]
            self._execute_3D(TV)
