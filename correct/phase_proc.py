"""
Utilities for working with phase data.

Code adapted from PAPER by Scott Giangrande et al

Adapted by Scott Collis and Scott Giangrande, refactored by Jonathan Helmus

"""

import copy
from time import time

import numpy as np
from numpy import ma
import glpk


def det_sys_phase(radar, ncp_lev=0.4, rhohv_lev=0.6,
                  ncp_field='norm_coherent_power', rhv_field='copol_coeff',
                  phidp_field='dp_phase_shift'):
    """
    Determine the system phase.

    Parameters
    ----------
    radar : Radar
        Radar object for which to determine the system phase.
    ncp_lev :
        Miminum normal coherence power level.  Regions below this value will
        not be included in the phase calculation.
    rhohv_lev :
        Miminum copolar coefficient level.  Regions below this value will not
        be included in the phase calculation.
    ncp_field, rhv_field, phidp_field : str
        Field names within the radar object which represent the normal
        coherence power, the copolar coefficient, and the differential phase
        shift.

    Returns
    -------
    sys_phase : float or None
        Estimate of the system phase.  None is not estimate can be made.

    """
    print "Unfolding"
    ncp = radar.fields[ncp_field]['data'][:, 30:]
    rhv = radar.fields[rhv_field]['data'][:, 30:]
    phidp = radar.fields[phidp_field]['data'][:, 30:]
    last_ray_idx = radar.sweep_info['sweep_end_ray_index']['data'][0]
    return _det_sys_phase(ncp, rhv, phidp, last_ray_idx, ncp_lev,
                          rhohv_lev)


def _det_sys_phase(ncp, rhv, phidp, last_ray_idx, ncp_lev=0.4,
                   rhv_lev=0.6):
    """ Determine the system phase, see :py:func:`det_sys_phase`. """
    good = False
    phases = []
    for radial in xrange(last_ray_idx + 1):
        meteo = np.logical_and(ncp[radial, :] > ncp_lev,
                               rhv[radial, :] > rhv_lev)
        mpts = np.where(meteo)
        if len(mpts[0]) > 25:
            good = True
            msmth_phidp = smooth_and_trim(phidp[radial, mpts[0]], 9)
            phases.append(msmth_phidp[0:25].min())
    if not(good):
        return None
    return np.median(phases)


def fzl_index(fzl, ranges, elevation, radar_height):
    """
    Return the index of the last gate below a given altitude.

    Parameters
    ----------
    fzl : float
        Maximum altitude.
    ranges : array
        Range to measurement volume/gate in meters.
    elevation : float
        Elevation of antenna in degrees.
    radar_height :
        Altitude of radar in meters.

    Returns
    -------
    idx : int
        Index of last gate which has an altitude below `fzl`.

    Notes
    -----
    Standard atmosphere is assumed, R = 4 / 3 * Re

    """
    Re = 6371.0 * 1000.0
    p_r = 4.0 * Re / 3.0
    z = radar_height + (ranges ** 2 + p_r ** 2 + 2.0 * ranges * p_r *
                        np.sin(elevation * np.pi / 180.0)) ** 0.5 - p_r
    return np.where(z < fzl)[0].max()


def det_process_range(radar, sweep, fzl, doc=10):
    """
    Determine the processing range for a given sweep.

    Queues the radar and returns the indices which can be used to slice
    the radar fields and select the desired sweep with gates which are
    below a given altitude.

    Parameters
    ----------
    radar : Radar
        Radar object from which ranges will be determined.
    sweep : int
        Sweep (0 indexed) for which to determine processing ranges.
    fzl : float
        Maximum altitude in meters. The determined range will not include
        gates which are above this limit.
    doc : int
        Minimum number of gates which will be excluded from the determined
        range.

    Returns
    -------
    gate_end : int
        Index of last gate below `fzl` and satisfying the `doc` parameter.
    ray_start : int
        Ray index which defines the start of the region.
    ray_end : int
        Ray index which defined the end of the region.

    """

    # determine the index of the last valid gate
    ranges = radar.range['data']
    elevation = radar.sweep_info['fixed_angle']['data'][sweep]
    radar_height = radar.location['altitude']['data']
    gate_end = fzl_index(fzl, ranges, elevation, radar_height)
    gate_end = min(gate_end, len(ranges) - doc)

    ray_start = radar.sweep_info['sweep_start_ray_index']['data'][sweep]
    ray_end = radar.sweep_info['sweep_end_ray_index']['data'][sweep]
    return gate_end, ray_start, ray_end


def snr(line, wl=11):
    """ Return the signal to noise ratio after smoothing. """
    signal = smooth_and_trim(line, window_len=wl)
    noise = smooth_and_trim(np.sqrt((line - signal) ** 2), window_len=wl)
    return abs(signal) / noise


def unwrap_masked(lon, centered=False, copy=True):
    """
    Unwrap a sequence of longitudes or headings in degrees.

    Parameters
    ----------
    lon : array
        Longtiudes or heading in degress. If masked output will also be
        masked.
    centered : bool, optional
        Center the unwrapping as close to zero as possible.
    copy : bool, optional.
        True to return a copy, False will avoid a copy when possible.

    Returns
    -------
    unwrap : array
        Array of unwrapped longtitudes or headings, in degrees.

    """
    masked_input = ma.isMaskedArray(lon)
    if masked_input:
        fill_value = lon.fill_value
        # masked_invalid loses the original fill_value (ma bug, 2011/01/20)
    lon = np.ma.masked_invalid(lon).astype(float)
    if lon.ndim != 1:
        raise ValueError("Only 1-D sequences are supported")
    if lon.shape[0] < 2:
        return lon
    x = lon.compressed()
    if len(x) < 2:
        return lon
    w = np.zeros(x.shape[0] - 1, int)
    ld = np.diff(x)
    np.putmask(w, ld > 180, -1)
    np.putmask(w, ld < -180, 1)
    x[1:] += (w.cumsum() * 360.0)
    if centered:
        x -= 360 * np.round(x.mean() / 360.0)
    if lon.mask is ma.nomask:
        lon[:] = x
    else:
        lon[~lon.mask] = x
    if masked_input:
        lon.fill_value = fill_value
        return lon
    else:
        return lon.filled(np.nan)


# this function adapted from the Scipy Cookbook:
# http://www.scipy.org/Cookbook/SignalSmooth
def smooth_and_trim(x, window_len=11, window='hanning'):
    """
    Smooth data using a window with requested size.

    This method is based on the convolution of a scaled window with the signal.
    The signal is prepared by introducing reflected copies of the signal
    (with the window size) in both ends so that transient parts are minimized
    in the begining and end part of the output signal.

    Parameters
    ----------
    x : array
        The input signal
    window_len: int
        The dimension of the smoothing window; should be an odd integer.
    window : str
        The type of window from 'flat', 'hanning', 'hamming', 'bartlett',
        'blackman' or 'sg_smooth'. A flat window will produce a moving
        average smoothing.

    Returns
    -------
    y : array
        The smoothed signal with length equal to the input signal.

    """
    if x.ndim != 1:
        raise ValueError("smooth only accepts 1 dimension arrays.")
    if x.size < window_len:
        raise ValueError("Input vector needs to be bigger than window size.")
    if window_len < 3:
        return x
    valid_windows = ['flat', 'hanning', 'hamming', 'bartlett', 'blackman',
                     'sg_smooth']
    if not window in valid_windows:
        raise ValueError("Window is on of " + ' '.join(valid_windows))

    s = np.r_[x[window_len - 1:0:-1], x, x[-1:-window_len:-1]]

    if window == 'flat':  # moving average
        w = np.ones(window_len, 'd')
    elif window == 'sg_smooth':
        w = np.array([0.1, .25, .3, .25, .1])
    else:
        w = eval('np.' + window + '(window_len)')

    y = np.convolve(w / w.sum(), s, mode='valid')

    return y[window_len / 2:len(x) + window_len / 2]


def sobel(x, window_len=11):
    """
    Sobel differential filter, useful for calculating KDP.

    Parameters
    ----------
    x : array
        Input signal.
    window_len : int
        Length of window.

    Returns
    -------
    output : array
        Differential signal (Unscaled for gate spacing)

    """
    s = np.r_[x[window_len - 1:0:-1], x, x[-1:-window_len:-1]]
    w = 2.0 * np.arange(window_len) / (window_len-1.0) - 1.0
    w = w / (abs(w).sum())
    y = np.convolve(w, s, mode='valid')
    return (-1.0 * y[window_len / 2:len(x) + window_len / 2] /
            (window_len / 3.0))


def noise(line, wl=11):
    """ Return the noise after smoothing. """
    signal = smooth_and_trim(line, window_len=wl)
    noise = np.sqrt((line - signal) ** 2)
    return noise


def get_phidp_unf(radar, ncp_lev=0.4, rhohv_lev=0.6, debug=False, ncpts=20,
                  doc=-10, overide_sys_phase=False, sys_phase=-135,
                  nowrap=None, refl_field='reflectivity_horizontal',
                  ncp_field='norm_coherence_power', rhv_field='copol_coeff',
                  phidp_field='dp_phase_shift'):
    """
    Get Unfolded Phi differential phase

    Parameters
    ----------
    radar : Radar
        The input radar.
    ncp_lev :
        Miminum normal coherence power level.  Regions below this value will
        not be included in the calculation.
    rhohv_lev :
        Miminum copolar coefficient level.  Regions below this value will not
        be included in the calculation.
    debug : bool, optioanl
        True to print debugging information, False to supress printing.
    ncpts : int
        Minimum number of points in a ray.  Regions within a ray smaller than
        this or beginning before this gate number are excluded from
        calculations.
    doc : int or None.
        Index of first gate not to include in field data, None include all.
    overide_sys_phase : bool, optional
        True to use `sys_phase` as the system phase. False will determine a
        value automatically.
    sys_phase : float, optional
        System phase, not used if overide_sys_phase is False.
    nowrap : or None
        Gate number where unwrapping should begin. `None` will unwrap all
        gates.
    refl_field ncp_field, rhv_field, phidp_field : str
        Field names within the radar object which represent the horizonal
        reflectivity, normal coherence power, the copolar coefficient, and the
        differential phase shift.

    Returns
    -------
    cordata : array
        Unwrapped phi differential phase.

    """

    if 'nowrap' is not None:
        print "Starting late"

    if doc is not None:
        my_phidp = radar.fields[phidp_field]['data'][:, 0:doc]
        my_rhv = radar.fields[rhv_field]['data'][:, 0:doc]
        my_ncp = radar.fields[ncp_field]['data'][:, 0:doc]
        my_z = radar.fields[refl_field]['data'][:, 0:doc]
    else:
        my_phidp = radar.fields[phidp_field]['data']
        my_rhv = radar.fields[rhv_field]['data']
        my_ncp = radar.fields[ncp_field]['data']
        my_z = radar.fields[refl_field]['data']
    t = time()
    if overide_sys_phase:
        system_zero = sys_phase
    else:
        system_zero = det_sys_phase(
            radar, ncp_field=ncp_field, rhv_field=rhv_field,
            phidp_field=phidp_field)
        if system_zero is None:
            system_zero = sys_phase
    cordata = np.zeros(my_rhv.shape, dtype=float)
    for radial in range(my_rhv.shape[0]):
        my_snr = snr(my_z[radial, :])
        notmeteo = np.logical_or(np.logical_or(
            my_ncp[radial, :] < ncp_lev,
            my_rhv[radial, :] < rhohv_lev), my_snr < 10.0)
        x_ma = ma.masked_where(notmeteo, my_phidp[radial, :])
        try:
            ma.notmasked_contiguous(x_ma)
            for slc in ma.notmasked_contiguous(x_ma):
                # so trying to get rid of clutter and small things that
                # should not add to phidp anyway
                if slc.stop - slc.start < ncpts or slc.start < ncpts:
                    x_ma.mask[slc.start - 1:slc.stop + 1] = True
            c = 0
        except TypeError:  # non sequence, no valid regions
            c = 1  # ie do nothing
            x_ma.mask[:] = True
        except AttributeError:
            # sys.stderr.write('No Valid Regions, ATTERR \n ')
            # sys.stderr.write(myfile.times['time_end'].isoformat() + '\n')
            # print x_ma
            # print x_ma.mask
            c = 1  # also do nothing
            x_ma.mask = True
        if 'nowrap' is not None:
            # Start the unfolding a bit later in order to avoid false
            # jumps based on clutter
            unwrapped = copy.deepcopy(x_ma)
            end_unwrap = unwrap_masked(x_ma[nowrap::], centered=False)
            unwrapped[nowrap::] = end_unwrap
        else:
            unwrapped = unwrap_masked(x_ma, centered=False)
        #end so no clutter expected
        system_max = unwrapped[np.where(np.logical_not(
            notmeteo))][-10:-1].mean() - system_zero
        unwrapped_fixed = np.zeros(len(x_ma), dtype=float)
        based = unwrapped-system_zero
        based[0] = 0.0
        notmeteo[0] = False
        based[-1] = system_max
        notmeteo[-1] = False
        unwrapped_fixed[np.where(np.logical_not(based.mask))[0]] = \
            based[np.where(np.logical_not(based.mask))[0]]
        if len(based[np.where(np.logical_not(based.mask))[0]]) > 11:
            unwrapped_fixed[np.where(based.mask)[0]] = \
                np.interp(np.where(based.mask)[0],
                          np.where(np.logical_not(based.mask))[0],
                          smooth_and_trim(based[np.where(
                              np.logical_not(based.mask))[0]]))
        else:
            unwrapped_fixed[np.where(based.mask)[0]] = \
                np.interp(np.where(based.mask)[0],
                          np.where(np.logical_not(based.mask))[0],
                          based[np.where(np.logical_not(based.mask))[0]])
        if c != 1:
            cordata[radial, :] = unwrapped_fixed
        else:
            cordata[radial, :] = np.zeros(my_rhv.shape[1])
    if debug:
        print "Exec time: ", time() - t
    return cordata


def construct_A_matrix(n_gates, filt):
    """
    Construct a row-augmented A matrix. Equation 5 in Giangrande et al, 2012.

    A is a block matrix given by:

    .. math::

        \\bf{A} = \\begin{bmatrix} \\bf{I} & \\bf{-I} \\\\\\\\
                  \\bf{-I} & \\bf{I} \\\\\\\\ \\bf{Z}
                  & \\bf{M} \\end{bmatrix}

    where
        :math:`\\bf{I}` is the identity matrix
        :math:`\\bf{Z}` is a matrix of zeros
        :math:`\\bf{M}` contains our differential constraints.

    Each block is of shape n_gates by n_gates making
    shape(:math:`\\bf{A}`) = (3 * n, 2 * n).

    Note that :math:`\\bf{M}` contains some side padding to deal with edge
    issues

    Parameters
    ----------
    n_gates : int
        Number of gates, determines size of identity matrix
    filt : array
        Input filter.

    Returns
    -------
    a : matrix
        Row-augmented A matrix.

    """
    Identity = np.eye(n_gates)
    filter_length = len(filt)
    M_matrix_middle = np.diag(np.ones(n_gates - filter_length + 1), k=0) * 0.0
    posn = np.linspace(-1.0 * (filter_length - 1) / 2, (filter_length - 1)/2,
                       filter_length)
    for diag in range(filter_length):
        M_matrix_middle = M_matrix_middle + np.diag(np.ones(
            n_gates - filter_length + 1 - np.abs(posn[diag])),
            k=posn[diag]) * filt[diag]
    side_pad = (filter_length - 1) / 2
    M_matrix = np.bmat(
        [np.zeros([n_gates-filter_length + 1, side_pad], dtype=float),
         M_matrix_middle, np.zeros([n_gates-filter_length+1, side_pad],
         dtype=float)])
    Z_matrix = np.zeros([n_gates - filter_length + 1, n_gates])
    return np.bmat([[Identity, -1.0 * Identity], [Identity, Identity],
                   [Z_matrix, M_matrix]])


def construct_B_vectors(phidp_mod, z_mod, filt, coef=0.914, dweight=60000.0):
    """
    Construct B vectors.  See Giangrande et al, 2012.

    Parameters
    ----------
    phidp_mod : 2D array
        Phi differential phases.
    z_mod : 2D array.
       Reflectivity, modified as needed.
    filt : array
        Input filter.
    coef : float, optional.
        Cost coefficients.
    dweight : float, optional.
        Weights.

    Returns
    -------
    b : matrix
        Matrix containing B vectors.

    """
    n_gates = phidp_mod.shape[1]
    n_rays = phidp_mod.shape[0]
    filter_length = len(filt)
    side_pad = (filter_length - 1) / 2
    top_of_B_vectors = np.bmat([[-phidp_mod, phidp_mod]])
    data_edges = np.bmat([phidp_mod[:, 0:side_pad],
                         np.zeros([n_rays, n_gates-filter_length+1]),
                         phidp_mod[:, -side_pad:]])
    ii = filter_length - 1
    jj = data_edges.shape[1] - 1
    list_corrl = np.zeros([n_rays, jj - ii + 1])
    for count in range(list_corrl.shape[1]):
        list_corrl[:, count] = -1.0 * (
            np.array(filt) * (np.asarray(
                data_edges))[:, count:count+ii+1]).sum(axis=1)

    sct = (((10.0 ** (0.1 * z_mod)) ** coef / dweight))[:, side_pad: -side_pad]
    sct[np.where(sct < 0.0)] = 0.0
    sct[:, 0:side_pad] = list_corrl[:, 0:side_pad]
    sct[:, -side_pad:] = list_corrl[:, -side_pad:]
    B_vectors = np.bmat([[top_of_B_vectors, sct]])
    return B_vectors


def LP_solver(A_Matrix, B_vectors, weights, it_lim=7000, presolve=True,
              really_verbose=False):
    """
    Solve the Linear Programming problem, see Giangrande et al, 2012.

    Parameters
    ----------
    A_Matrix : matrix
        Row augmented A matrix, see :py:func:`construct_A_matrix`
    B_vectors : matrix
        Matrix containing B vectors, see :py:func:`construct_B_vectors`
    weights : array
        Weights.
    it_lim : int
        Simplex iteration limit.
    presolve : bool
        True to use the LP presolver.
    really_verbose : bool
        True to print LPX messaging. False to suppress.

    Returns
    -------
    soln : array
        Solution to LP problem.

    """
    if really_verbose:
        message_state = glpk.LPX.MSG_ON
    else:
        message_state = glpk.LPX.MSG_OFF
    n_gates = weights.shape[1]/2
    n_rays = B_vectors.shape[0]
    mysoln = np.zeros([n_rays, n_gates])
    lp = glpk.LPX()  # Create empty problem instance
    lp.name = 'LP_MIN'  # Assign symbolic name to problem
    lp.obj.maximize = False  # Set this as a maximization problem
    lp.rows.add(2 * n_gates + n_gates - 4)  # Append rows
    lp.cols.add(2 * n_gates)
    glpk.env.term_on = True
    for cur_row in range(2 * n_gates + n_gates - 4):
        lp.rows[cur_row].matrix = list(np.squeeze(np.asarray(
            A_Matrix[cur_row, :])))
    for i in range(2 * n_gates):
        lp.cols[i].bounds = 0.0, None
    for raynum in range(n_rays):
        this_soln = np.zeros(n_gates)
        for i in range(2 * n_gates + n_gates - 4):
            lp.rows[i].bounds = B_vectors[raynum, i], None
        for i in range(2 * n_gates):
            lp.obj[i] = weights[raynum, i]
        lp.simplex(msg_lev=message_state, meth=glpk.LPX.PRIMAL,
                   it_lim=it_lim, presolve=presolve)
        for i in range(n_gates):
            this_soln[i] = lp.cols[i+n_gates].primal
        mysoln[raynum, :] = smooth_and_trim(this_soln, window_len=5,
                                            window='sg_smooth')
    return mysoln


def phase_proc(radar, offset, debug=False, self_const=60000.0,
               low_z=10.0, high_z=53.0, min_phidp=0.01, min_ncp=0.5,
               min_rhv=0.8, fzl=4000.0, sys_phase=0.0,
               overide_sys_phase=False, nowrap=None, really_verbose=False,
               refl_field='reflectivity_horizontal',
               ncp_field='norm_coherent_power', rhv_field='copol_coeff',
               phidp_field='dp_phase_shift', kdp_field='diff_phase'):
    """
    Phase process using a LP method[1].

    Parameters
    ----------
    radar : Radar
        Input radar.
    offset : float
        Reflectivity offset in dBz.
    debug : bool, optional
        True to print debugging information.
    self_const : float, optional
        Self consistency factor.
    low_z : float
        Low limit for reflectivity. Reflectivity below this value is set to
        this limit.
    high_z : float
        High limit for reflectivity.  Reflectivity above this value is set to
        this limit.
    min_phidp : float
        Minimum Phi differential phase.
    min_ncp : float
        Minimum normal coherent power.
    min_rhv : float
        Minimum copolar coefficient.
    fzl :
        Maximum altitude.
    sys_phase : float
        System phase in degrees.
    overide_sys_phase: bool.
        True to use `sys_phase` as the system phase.  False will calculate a
        value automatically.
    nowrap : int or None.
        Gate number to begin phase unwrapping.  None will unwrap all phases.
    really_verbose : bool
        True to print LPX messaging. False to suppress.
    refl_field, ncp_field, rhv_field, phidp_field, kdp_field: str
        Name of field in radar which contains the horizonal reflectivity,
        normal cohernect power, copolar coefficient, differential phase shift,
        and differential phase.

    Returns
    -------
    reproc_phase : dict
        Field dictionary containing processed differential phase shifts.
    sob_kdp : dict
        Field dictionary containing recalculated differential phases.

    References
    ----------
    [1] Giangrande, S.E., R. McGraw, and L. Lei, 2012: An Application of
    Linear Programming to Polarimetric Radar Differential Phase Processing.
    Submitted, J. Atmos. and Oceanic Tech.

    """

    # prepare reflectivity field
    refl = copy.deepcopy(radar.fields[refl_field]['data']) + offset
    is_low_z = (refl) < low_z
    is_high_z = (refl) > high_z
    refl[np.where(is_high_z)] = high_z
    refl[np.where(is_low_z)] = low_z
    z_mod = refl

    # unfold Phi_DP
    if debug:
        print('Unfolding')
    my_unf = get_phidp_unf(radar, ncp_lev=min_ncp, rhohv_lev=min_rhv,
                           debug=debug, ncpts=2, doc=None,
                           sys_phase=sys_phase, nowrap=nowrap,
                           overide_sys_phase=overide_sys_phase,
                           refl_field=refl_field, ncp_field=ncp_field,
                           rhv_field=rhv_field, phidp_field=phidp_field)
    my_new_ph = copy.deepcopy(radar.fields[phidp_field])
    my_unf[:, -1] = my_unf[:, -2]
    my_new_ph['data'] = my_unf
    radar.fields.update({'unf_dp_phase_shift': my_new_ph})

    phidp_mod = copy.deepcopy(radar.fields['unf_dp_phase_shift']['data'])
    phidp_neg = phidp_mod < min_phidp
    phidp_mod[np.where(phidp_neg)] = min_phidp

    # process
    proc_ph = copy.deepcopy(radar.fields[phidp_field])
    proc_ph['data'] = phidp_mod
    St_Gorlv_differential_5pts = [-.2, -.1, 0, .1, .2]
    for sweep in range(len(radar.sweep_info['sweep_start_ray_index']['data'])):
        if debug:
            print "Doing ", sweep
        end_gate, start_ray, end_ray = det_process_range(
            radar, sweep, fzl, doc=15)
        start_gate = 0

        A_Matrix = construct_A_matrix(
            len(radar.range['data'][start_gate:end_gate]),
            St_Gorlv_differential_5pts)

        B_vectors = construct_B_vectors(
            phidp_mod[start_ray:end_ray, start_gate:end_gate],
            z_mod[start_ray:end_ray, start_gate:end_gate],
            St_Gorlv_differential_5pts, dweight=self_const)

        weights = np.ones(
            phidp_mod[start_ray:end_ray, start_gate:end_gate].shape)

        nw = np.bmat([weights, np.zeros(weights.shape)])

        mysoln = LP_solver(A_Matrix, B_vectors, nw, it_lim=7000,
                           presolve=True, really_verbose=really_verbose)

        proc_ph['data'][start_ray:end_ray, start_gate:end_gate] = mysoln

    last_gates = proc_ph['data'][start_ray:end_ray, -16]
    proc_ph['data'][start_ray:end_ray, -16:] = \
        np.meshgrid(np.ones([16]), last_gates)[1]
    proc_ph['valid_min'] = 0.0
    proc_ph['valid_max'] = 400.0

    # prepare output
    kdp = np.zeros(radar.fields[phidp_field]['data'].shape)
    for i in range(kdp.shape[0]):
        kdp[i, :] = (sobel(proc_ph['data'][i, :], window_len=35) /
                     (2.0 * (radar.range['data'][1] -
                     radar.range['data'][0]) / 1000.0))
    try:
        sob_kdp = copy.deepcopy(radar.fields[kdp_field])
        sob_kdp['data'] = kdp
        sob_kdp['valid_min'] = 0.0
        sob_kdp['valid_max'] = 20.0
    except KeyError:
        sob_kdp = copy.deepcopy(radar.fields[phidp_field])
        sob_kdp['data'] = kdp
        sob_kdp['valid_min'] = 0.0
        sob_kdp['valid_max'] = 20.0
        sob_kdp['standard_name'] = "specific_differential_phase_hv"
        sob_kdp['long_name'] = "specific_differential_phase_hv"
        sob_kdp['units'] = "degrees/km"
        sob_kdp['least_significant_digit'] = 2
        sob_kdp['_FillValue'] = -9999.

    return proc_ph, sob_kdp