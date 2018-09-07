#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Jun  9 11:33:09 2017

@author: omaier
"""

import numpy as np
import matplotlib.pyplot as plt
plt.ion()
import numexpr as ne
DTYPE = np.complex64
class constraint:
    def __init__(self, min_val=-np.inf, max_val=np.inf, real_const=False):
        self.min = min_val
        self.max = max_val
        self.real = real_const
    def update(self,scale):
        self.min = self.min/scale
        self.max = self.max/scale


class IRLL_Model:
    def __init__(self, fa, fa_corr, TR,tau,td,
               NScan,NSlice,dimY,dimX, Nproj,Nproj_measured,scale,images):
        self.constraints = []
        self.NSlice = NSlice
        self.TR = TR
        self.fa = fa
        self.fa_corr = fa_corr
        self.T1_sc = 1
        self.M0_sc = 1
        self.Nproj_measured = Nproj_measured
        self.tau = tau
        self.td = td
        self.NLL = NScan
        self.Nproj = Nproj
        self.dimY = dimY
        self.dimX = dimX
        phi_corr = np.real(fa)*np.real(fa_corr) + 1j*np.imag(fa)*np.imag(fa_corr)
        self.sin_phi = np.sin(phi_corr)
        self.cos_phi = np.cos(phi_corr)
        self.M0_sc = 1
        self.T1_sc = 1
        self.scale = 100


        test_T1 = np.exp(-self.scale/np.reshape(np.linspace(10,5500,dimX*dimY*NSlice),(NSlice,dimX,dimY)))
        test_M0 = 1
        G_x = self.execute_forward_3D(np.array([test_M0/self.M0_sc*np.ones((NSlice,dimY,dimX),dtype=DTYPE),1/self.T1_sc*test_T1*np.ones((NSlice,dimY,dimX),dtype=DTYPE)],dtype=DTYPE))
        self.M0_sc = self.M0_sc*np.median(np.abs(images))/np.median(np.abs(G_x))

        DG_x =  self.execute_gradient_3D(np.array([test_M0/self.M0_sc*np.ones((NSlice,dimY,dimX),dtype=DTYPE),1/self.T1_sc*test_T1*np.ones((NSlice,dimY,dimX),dtype=DTYPE)],dtype=DTYPE))
        self.T1_sc = self.T1_sc*np.linalg.norm(np.abs(DG_x[0,...]))/np.linalg.norm(np.abs(DG_x[1,...]))

        self.T1_sc = self.T1_sc
        DG_x =  self.execute_gradient_3D(np.array([test_M0/self.M0_sc*np.ones((NSlice,dimY,dimX),dtype=DTYPE),1/self.T1_sc*test_T1*np.ones((NSlice,dimY,dimX),dtype=DTYPE)],dtype=DTYPE))
        self.guess = np.array([1/self.M0_sc*np.ones((NSlice,dimY,dimX),dtype=DTYPE),np.exp(-self.scale/800)/self.T1_sc*np.ones((NSlice,dimY,dimX),dtype=DTYPE)])
        self.constraints.append(constraint(-300,300,False)  )
        self.constraints.append(constraint(np.exp(-self.scale/10)/self.T1_sc, np.exp(-self.scale/5500)/self.T1_sc,True))

    def plot_unknowns(self,x,dim_2D=False):
        M0 = np.abs(x[0,...]*self.M0_sc)
        T1 = np.abs(x[1,...]*self.T1_sc)
        M0_min = M0.min()
        M0_max = M0.max()
        T1_min = T1.min()
        T1_max = T1.max()

        if dim_2D:
             if not self.figure:
                 plt.ion()
                 self.figure, self.ax = plt.subplots(1,2,figsize=(12,5))
                 self.M0_plot = self.ax[0].imshow(np.transpose(M0))
                 self.T1_plot = self.ax[1].imshow(np.transpose(T1))
                 plt.draw()
                 plt.pause(1e-10)
             else:
                 self.M0_plot.set_data(np.transpose(M0))
                 self.M0_plot.set_clim([M0_min,M0_max])
                 self.T1_plot.set_data(np.transpose(T1))
                 self.T1_plot.set_clim([T1_min,T1_max])
                 plt.draw()
                 plt.pause(1e-10)
        else:
             if not self.figure:
                 plt.ion()
                 self.figure, self.ax = plt.subplots(1,2,figsize=(12,5))
                 self.M0_plot=self.ax[0].imshow(np.transpose(M0[int(self.Nislice/2),...]))
                 self.ax[0].set_title('Proton Density in a.u.')
                 self.ax[0].axis('off')
                 self.figure.colorbar(self.M0_plot,ax=self.ax[0])
                 self.T1_plot=self.ax[1].imshow(np.transpose(T1[int(self.Nislice/2),...]))
                 self.ax[1].set_title('T1 in  ms')
                 self.ax[1].axis('off')
                 self.figure.colorbar(self.T1_plot,ax=self.ax[1])
                 self.figure.tight_layout()
                 plt.draw()
                 plt.pause(1e-10)
             else:
                 self.M0_plot.set_data(np.transpose(M0[int(self.Nislice/2),...]))
                 self.M0_plot.set_clim([M0_min,M0_max])
                 self.T1_plot.set_data(np.transpose(T1[int(self.Nislice/2),...]))
                 self.T1_plot.set_clim([T1_min,T1_max])
                 plt.draw()
                 plt.pause(1e-10)
    def execute_forward_2D(self, x, islice):
        S = np.zeros((self.NLL,self.Nproj,self.dimY,self.dimX),dtype=DTYPE)
        M0_sc = self.M0_sc
        TR = self.TR
        tau = self.tau
        td = self.td
        sin_phi = self.sin_phi[islice,...]
        cos_phi = self.cos_phi[islice,...]
        N = self.Nproj_measured
        scale = self.scale
        Efit = x[1,...]*self.T1_sc
        Etau = Efit**(tau/scale)
        Etr = Efit**(TR/scale)
        Etd = Efit**(td/scale)
        M0 = x[0,...]
        M0_sc = self.M0_sc
        F = (1 - Etau)/(1-Etau*cos_phi)
        Q = (-Etr*Etd*F*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi + Etr*Etd - 2*Etd + 1)/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)
        Q_F = Q-F

        for i in range(self.NLL):
          for j in range(self.Nproj):
                n = i*self.Nproj+j+1
                S[i,j,...] = M0*M0_sc*((Etau*cos_phi)**(n - 1)*Q_F + F)*sin_phi

        return np.array(np.mean(S,axis=1,dtype=np.complex256),dtype=DTYPE)
    def execute_gradient_2D(self, x, islice):
        grad = np.zeros((2,self.NLL,self.Nproj,self.dimY,self.dimX),dtype=DTYPE)
        M0_sc = self.M0_sc
        TR = self.TR
        tau = self.tau
        td = self.td
        sin_phi = self.sin_phi[islice,...]
        cos_phi = self.cos_phi[islice,...]
        N = self.Nproj_measured
        scale = self.scale
        Efit = x[1,...]*self.T1_sc
        Etau = Efit**(tau/scale)
        Etr = Efit**(TR/scale)
        Etd = Efit**(td/scale)

        M0 = x[0,...]
        M0_sc = self.M0_sc


        F = (1 - Etau)/(1-Etau*cos_phi)
        Q = (-Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)
        Q_F = Q-F
        tmp1 = ((-TR*Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)\
                    *cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) + TR*Etr*Etd/(x[1,...]*scale) - tau*Etr*Etau*Etd*(-Etau + 1)*\
                    (-(Etau*cos_phi)**(N - 1) + 1)*cos_phi**2/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) + \
                    tau*Etr*Etau*Etd*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) + \
                    tau*Etr*Etd*(Etau*cos_phi)**(N - 1)*(N - 1)*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) - \
                    td*Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) +\
                    td*Etr*Etd/(x[1,...]*scale) - 2*td*Etd/(x[1,...]*scale))/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1) +\
                    (-TR*Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi/(x[1,...]*scale) - tau*Etr*Etd*(Etau*cos_phi)**(N - 1)*(N - 1)\
                     *cos_phi/(x[1,...]*scale) - td*Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi/(x[1,...]*scale))*(-Etr*Etd*(-Etau + 1)\
                              *(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)/\
                     (Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)**2 - tau*Etau*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) \
                     + tau*Etau/(x[1,...]*scale*(-Etau*cos_phi + 1)))

        tmp2 = tau*Etau*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) - \
                     tau*Etau/(x[1,...]*scale*(-Etau*cos_phi + 1))

        tmp3 =  (-(-Etau + 1)/(-Etau*cos_phi + 1) \
                               + (-Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)\
                               /(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1))/(x[1,...]*scale)
        for i in range(self.NLL):
          for j in range(self.Nproj):
                n = i*self.Nproj+j+1

                grad[0,i,j,...] = M0_sc*((Etau*cos_phi)**(n - 1)*Q_F + F)*sin_phi

                grad[1,i,j,...] =M0*M0_sc*((Etau*cos_phi)**(n - 1)*tmp1 + tmp2 + tau*(Etau*cos_phi)**(n - 1)*(n - 1)*tmp3)*sin_phi

        return np.array(np.mean(grad,axis=2,dtype=np.complex256),dtype=DTYPE)

    def execute_forward_3D(self, x):
        S = np.zeros((self.NLL,self.Nproj,self.NSlice,self.dimY,self.dimX),dtype=DTYPE)

        TR = self.TR
        tau = self.tau
        td = self.td
        sin_phi = self.sin_phi
        cos_phi = self.cos_phi
        N = self.Nproj_measured
        scale = self.scale
        Efit = x[1,...]*self.T1_sc
        Etau = Efit**(tau/scale)
        Etr = Efit**(TR/scale)
        Etd = Efit**(td/scale)
        M0 = x[0,...]
        M0_sc = self.M0_sc

        F = (1 - Etau)/(1-Etau*cos_phi)
        Q = (-Etr*Etd*F*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi + Etr*Etd - 2*Etd + 1)/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)
        Q_F = Q-F

        def numexpeval_S(M0,M0_sc,sin_phi,cos_phi,n,Q_F,F,Etau):
            return ne.evaluate("M0*M0_sc*((Etau*cos_phi)**(n - 1)*Q_F + F)*sin_phi")
        for i in range(self.NLL):
            for j in range(self.Nproj):
                  n = i*self.Nproj+j+1
                  S[i,j,...] = numexpeval_S(M0,M0_sc,sin_phi,cos_phi,n,Q_F,F,Etau)

        return np.array(np.mean(S,axis=1,dtype=np.complex256),dtype=DTYPE)

    def execute_gradient_3D(self, x):
        grad = np.zeros((2,self.NLL,self.Nproj,self.NSlice,self.dimY,self.dimX),dtype=DTYPE)
        M0_sc = self.M0_sc
        TR = self.TR
        tau = self.tau
        td = self.td
        sin_phi = self.sin_phi
        cos_phi = self.cos_phi
        N = self.Nproj_measured
        scale = self.scale
        Efit = x[1,...]*self.T1_sc
        Etau = Efit**(tau/scale)
        Etr = Efit**(TR/scale)
        Etd = Efit**(td/scale)
        M0 = x[0,...]
        M0_sc = self.M0_sc

        F = (1 - Etau)/(1-Etau*cos_phi)
        Q = (-Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)
        Q_F = Q-F

        tmp1 = ((-TR*Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)\
                    *cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) + TR*Etr*Etd/(x[1,...]*scale) - tau*Etr*Etau*Etd*(-Etau + 1)*\
                    (-(Etau*cos_phi)**(N - 1) + 1)*cos_phi**2/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) + \
                    tau*Etr*Etau*Etd*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) + \
                    tau*Etr*Etd*(Etau*cos_phi)**(N - 1)*(N - 1)*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) - \
                    td*Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)) +\
                    td*Etr*Etd/(x[1,...]*scale) - 2*td*Etd/(x[1,...]*scale))/(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1) +\
                    (-TR*Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi/(x[1,...]*scale) - tau*Etr*Etd*(Etau*cos_phi)**(N - 1)*(N - 1)\
                     *cos_phi/(x[1,...]*scale) - td*Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi/(x[1,...]*scale))*(-Etr*Etd*(-Etau + 1)\
                              *(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)/\
                     (Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1)**2 - tau*Etau*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) \
                     + tau*Etau/(x[1,...]*scale*(-Etau*cos_phi + 1)))

        tmp2 = tau*Etau*(-Etau + 1)*cos_phi/(x[1,...]*scale*(-Etau*cos_phi + 1)**2) - \
                     tau*Etau/(x[1,...]*scale*(-Etau*cos_phi + 1))

        tmp3 =  (-(-Etau + 1)/(-Etau*cos_phi + 1) \
                               + (-Etr*Etd*(-Etau + 1)*(-(Etau*cos_phi)**(N - 1) + 1)*cos_phi/(-Etau*cos_phi + 1) + Etr*Etd - 2*Etd + 1)\
                               /(Etr*Etd*(Etau*cos_phi)**(N - 1)*cos_phi + 1))/(x[1,...]*scale)

        def numexpeval_M0(M0_sc,sin_phi,cos_phi,n,Q_F,F,Etau):
            return ne.evaluate("M0_sc*((Etau*cos_phi)**(n - 1)*Q_F + F)*sin_phi")
        def numexpeval_T1(M0,M0_sc,Etau,cos_phi,sin_phi,n,tmp1,tmp2,tmp3,tau):
            return ne.evaluate("M0*M0_sc*((Etau*cos_phi)**(n - 1)*tmp1 + tmp2 + tau*(Etau*cos_phi)**(n - 1)*(n - 1)*tmp3)*sin_phi")

        for i in range(self.NLL):
            for j in range(self.Nproj):
                n = i*self.Nproj+j+1

                grad[0,i,j,...] = numexpeval_M0(M0_sc,sin_phi,cos_phi,n,Q_F,F,Etau)
                grad[1,i,j,...] = numexpeval_T1(M0,M0_sc,Etau,cos_phi,sin_phi,n,tmp1,tmp2,tmp3,tau)

        print('Grad Scaling', np.linalg.norm(np.abs(np.mean(grad,axis=2)[0,...]))/np.linalg.norm(np.abs(np.mean(grad,axis=2)[1,...])))

        return np.array(np.mean(grad,axis=2,dtype=np.complex256),dtype=DTYPE)
