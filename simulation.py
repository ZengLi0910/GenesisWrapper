import os
import h5py
import numpy as np
import numpy.fft as fft
from scipy.constants import c

from . import averagePower
from . import parser
from .gaussfit import GaussFit

_xy = ('x', 'y',)

class GenesisSimulation:

    comment_chars = ('!',)
    default_dict = {
            'npart': 8192.,
            'sample': 2.,
            }
    warn_geo = True

    def __init__(self, infile, _file_=None, max_time_len=None, croptime=None):
        self.croptime = croptime # Wierd bug

        if _file_ is None:
            self.infile = infile
        else:
            self.infile = os.path.join(os.path.dirname(_file_), infile)

        self.input = parser.GenesisInputParser(self.infile)# , self.comment_chars, self.default_dict)
        dirname = os.path.dirname(self.infile)
        self.outfile = os.path.join(dirname, self.input['setup']['rootname']+'.out.h5')

        self._dict = {}
        zshape, tshape = self['Field/power'].shape
        try:
            self.zplot = self['Global/zplot']
        except KeyError:
            print('Old version of genesis. No zplot available.')

            if zshape == self['Lattice/z'].shape[0]+1:
                print('AAArGG')
                self.zplot = np.append(self['Lattice/z'], self['Lattice/z'][-1]+self['Lattice/dz'][-1])
            else:
                output_step = int(self.input['track']['output_step'])
                self.zplot = self['Lattice/z'][::output_step]
        if zshape != self.zplot.shape[0]:
            print('error', zshape, self.zplot.shape[0])
            import pdb; pdb.set_trace()

        time = self['Global/time']

        if time.shape == ():
            time = np.arange(0, tshape, dtype=float)*self['Global/sample']*self['Global/lambdaref']/c
        self.time = time

        if GenesisSimulation.warn_geo:
            print('Warning, adjusting for geometric emittance in buggy genesis version')
            GenesisSimulation.warn_geo = False




        self._powerfit = None
        self._gaussian_pulselength = None

        # Moved to properties
        self._beta_twiss, self._alpha_twiss, self._gamma_twiss = None, None, None
        self._geom_emittance = None

    @property
    def gaussian_pulselength(self):
        if self._gaussian_pulselength is None:
            self._gaussian_pulselength = self.powerfit.sigma
        return self._gaussian_pulselength

    @property
    def powerfit(self):
        if self._powerfit is None:
            self._powerfit = GaussFit(self.time, self['Field/power'][-1,:])
        return self._powerfit

    @property
    def beta_twiss(self):
        if self._beta_twiss is None:
            self._beta_twiss = {x: self['Beam/%ssize' % x][0,:]**2 / self.geom_emittance[x] for x in _xy}
        return self._beta_twiss

    @property
    def alpha_twiss(self):
        if self._alpha_twiss is None:
            self._alpha_twiss = {x: self['Beam/alpha%s' % x][0,:] for x in _xy}
        return self._alpha_twiss

    @property
    def gamma_twiss(self):
        if self._gamma_twiss is None:
            self._gamma_twiss = {x: (1.+self.alpha_twiss[x]**2)/self.beta_twiss[x] for x in _xy}
        return self._gamma_twiss

    @property
    def geom_emittance(self):
        if self._geom_emittance is None:
            self._geom_emittance = {x: self.get_geometric_emittance(x) for x in _xy}
        return self._geom_emittance


    def __getitem__(self, key):
        if key not in self._dict:
            with h5py.File(self.outfile, 'r') as ff:
                try:
                    raise_ = False
                    val = np.array(ff[key])
                except KeyError:
                    raise_ = True
                # Reduce verbosity
                if raise_:
                    raise KeyError('Key %s not found in %s' % (key, self.outfile))
                if len(val.shape) == 1:
                    val = np.squeeze(val)
                if val.ndim > 1 and self.croptime is not None:
                    val = val[:,:self.croptime]

                val.setflags(write=False) # Immutable array
                self._dict[key] = val
        return self._dict[key]

    def keys(self):
        with h5py.File(self.outfile, 'r') as ff:
            out = list(ff.keys())
        return out

    def print_tree(self):
        def name_and_size(key, ff):
            try:
                print((key, ff[key].shape, ff[key].dtype))
            except:
                print(key)

        with h5py.File(self.outfile, 'r') as ff:
            ff.visit(lambda x: name_and_size(x, ff))

    def get_rms_pulse_length(self, treshold=None):
        """
        Treshold: fraction of max value that is set to 0
        """
        time = self.time
        power = self['Field/power'][-1,:].copy()
        if treshold is not None:
            assert 0 <= treshold < 1
            power[power < (np.max(power)*treshold)] = 0
        return averagePower.get_rms_pulse_length(time, power)

    def get_total_pulse_energy(self):
        return averagePower.get_total_pulse_energy(self.time, self['Field/power'][-1,:])

    def get_m1(self, dimension, mu, mup):
        assert dimension in ('x', 'y')

        beta = self.beta_twiss[dimension][0]
        alpha = self.alpha_twiss[dimension][0]
        gamma = self.gamma_twiss[dimension][0]
        geom_emittance = self.geom_emittance[dimension]

        rms_bunch_length = self.get_rms_bunch_length()
        m1 = 1./geom_emittance * (beta*mup**2 + gamma*mu**2 + 2*alpha*mu*mup) * rms_bunch_length**2
        return m1

    def get_rms_bunch_length(self):
        zz = self.time*c
        curr = self['Beam/current']

        int_zz_sq = np.sum(zz**2*curr)/np.sum(curr)
        int_zz = np.sum(zz*curr)/np.sum(curr)

        return np.sqrt(int_zz_sq - int_zz**2)

    def get_geometric_emittance(self, dimension):
        assert dimension in ('x', 'y')
        geom_emittance = self['Beam/emit'+dimension][0,0]/self['Global/gamma0']

        if abs(geom_emittance - self.input['beam']['ex'])/geom_emittance < 1e-4:
            if self.warn_geo:
                print('Warning! Wrong emittance in output!')
                self.warn_geo = False
            geom_emittance /= self['Global/gamma0']

        return geom_emittance

    def get_average_beta(self, dimension):
        assert dimension in ('x', 'y')

        return np.nanmean(self.get_beta_func(dimension))

    def get_beta_func(self, dimension):
        assert dimension in ('x', 'y')

        xsize = self['Beam/%ssize' % dimension][:,0]

        # assert that beam is uniform along bunch.
        # Otherwise this method is wrong!
        s = self['Beam/%ssize' % dimension][0,:]
        assert np.nanmax(np.abs(s - np.nanmean(s))/np.nanmean(s)) < 1e-4

        em = self.get_geometric_emittance(dimension)
        return xsize**2/em

    def get_wavelength_spectrum(self, z_index=-1):
        field_abs = self['Field/intensity-farfield'][z_index,:]
        field_phase = self['Field/phase-farfield'][z_index,:]
        signal0 = np.sqrt(field_abs)*np.exp(1j*field_phase)
        l0 = self['Global/lambdaref']
        f0 = c/l0

        signal_fft = fft.fft(signal0)
        signal_fft_shift = fft.fftshift(signal_fft)

        dt = np.diff(self.time)[0] # "Sample" already included
        nq = 1/(2*dt)
        xx = np.linspace(f0-nq, f0+nq, signal_fft.size)

        return xx, np.abs(signal_fft_shift)

    def z_index(self, z):
        index = int(np.squeeze(np.argmin(np.abs(self.zplot-z))))
        if index in (0, len(self.zplot)-1):
            print('Warning: Index is at limit!')
        return index

    def t_index(self, t):
        index = int(np.squeeze(np.argmin(np.abs(self.time-t))))
        if index in (0, len(self.time)-1):
            print('Warning: Index is at limit!')
        return index

    def _get_vertical_size(self, dimension, index):
        assert dimension in ('x', 'y')
        tt = self['Field/%ssize' % dimension][index,:]
        power = self['Field/power'][index,:]
        return np.sum(tt*power)/np.sum(power)

    def xsize(self, index=-1):
        return self._get_vertical_size('x', index)

    def ysize(self, index=-1):
        return self._get_vertical_size('y', index)


# Obsolete
class InputParser(dict):

    def __init__(self, infile, comment_chars, default_dict):
        super().__init__(self)
        self.infile = infile
        self.comment_chars = comment_chars
        self.default_dict = default_dict

        with open(self.infile, 'r') as f:
            lines = f.readlines()

        self.update(default_dict)
        for line in lines:
            line = line.strip().replace(',','').replace(';','')
            if line and line[0] in self.comment_chars:
                pass
            elif '=' in line:
                attr = line.split('=')[0].strip()
                value = line.split('=')[-1].strip()
                try:
                    value = float(value)
                except ValueError:
                    pass
                self[attr] = value


    def estimate_memory(self):
        # From Sven
        n_slices = self['slen'] / self['lambda0'] / self['sample']
        memory_field = self['ngrid']**2*n_slices*16
        memory_beam = self['npart']*n_slices*6*8
        safety_factor = 1.5 # more accurate than factor of 2?

        return (memory_field+memory_beam)*safety_factor

