# Copyright 2019 NREL

# Licensed under the Apache License, Version 2.0 (the "License"); you may not use
# this file except in compliance with the License. You may obtain a copy of the
# License at http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

import numpy as np
import datetime
from ccblade import CCAirfoil, CCBlade
from scipy import interpolate, gradient
from WTC_toolbox import turbine as wtc_turbine

# Some useful constants
now = datetime.datetime.now()
pi = np.pi
rad2deg = np.rad2deg(1)
deg2rad = np.deg2rad(1)
rpm2RadSec = 2.0*(np.pi)/60.0
RadSec2rpm = 60/(2.0 * np.pi)

class Controller():
    """
    Class controller used to calculate controller tunings parameters
    """

    def __init__(self, controller_params):
        pass
        # turbine = wtc_turbine.Turbine(turbine_params)

        # Controller Flags
        self.LoggingLevel = controller_params['LoggingLevel']
        self.F_LPFType = controller_params['F_LPFType']
        self.F_NotchType = controller_params['F_NotchType']
        self.IPC_ControlMode = controller_params['IPC_ControlMode']
        self.VS_ControlMode = controller_params['VS_ControlMode']
        self.PC_ControlMode = controller_params['PC_ControlMode']
        self.Y_ControlMode = controller_params['Y_ControlMode']
        self.SS_Mode = controller_params['SS_Mode']
        self.WE_Mode = controller_params['WE_Mode']
        self.PS_Mode = controller_params['PS_Mode']

        # Necessary parameters
        self.zeta_pc = controller_params['zeta_pc']
        self.omega_pc = controller_params['omega_pc']
        self.zeta_vs = controller_params['zeta_vs']
        self.omega_vs = controller_params['omega_vs']

        # Optional parameters, default to standard if not defined
        if controller_params['min_pitch']:
            self.min_pitch = controller_params['min_pitch']
        else:
            self.min_pitch = 0.      # Default to zero degrees min pitch
        
        if controller_params['max_pitch']:
            self.max_pitch = controller_params['max_pitch']
        else:
            self.max_pitch = 90*deg2rad      # Default to 90 degrees max pitch
        
        if controller_params['ss_vsgain']:
            self.ss_vsgain = controller_params['ss_vsgain']
        else:
            self.ss_vsgain = 1.      # Default to 100% setpoint shift
        
        if controller_params['ss_pcgain']:
            self.ss_pcgain = controller_params['ss_pcgain']
        else:
            self.ss_pcgain = 0.001      # Default to 0.1% setpoint shift
        
        if controller_params['ss_cornerfreq']:
            self.ss_cornerfreq = controller_params['ss_cornerfreq']
        else:
            self.ss_cornerfreq = .62831850001     # Default to 10 second time constant 
        
        if controller_params['ps_percent']:
            self.ps_percent = controller_params['ps_percent']
        else:
            self.ps_percent = 0.75      # Default to 75% peak shaving



    def tune_controller(self, turbine):
        """
        Given a turbine model, tune the controller parameters
        """
        # -------------Load Parameters ------------- #
        # Re-define Turbine Parameters for shorthand
        J = turbine.J                           # Total rotor inertial (kg-m^2) 
        rho = turbine.rho                       # Air density (kg/m^3)
        R = turbine.rotor_radius                    # Rotor radius (m)
        Ar = np.pi*R**2                         # Rotor area (m^2)
        Ng = turbine.Ng                         # Gearbox ratio (-)
        rated_rotor_speed = turbine.rated_rotor_speed               # Rated rotor speed (rad/s)

        # Load controller parameters 
        #   - should be self.read_param_file() eventually, hard coded for now
        # self.controller_params(turbine)

        # -------------Define Operation Points ------------- #
        TSR_rated = rated_rotor_speed*R/turbine.v_rated  # TSR at rated

        # separate wind speeds by operation regions
        v_below_rated = np.arange(turbine.v_min,turbine.v_rated,0.5)             # below rated
        v_above_rated = np.arange(turbine.v_rated+0.5,turbine.v_max,0.5)             # above rated
        v = np.concatenate((v_below_rated, v_above_rated))

        # separate TSRs by operations regions
        TSR_below_rated = np.ones(len(v_below_rated))*turbine.Cp.TSR_opt # below rated     
        TSR_above_rated = rated_rotor_speed*R/v_above_rated                     # above rated
        TSR_op = np.concatenate((TSR_below_rated, TSR_above_rated))   # operational TSRs

        # Find expected operational Cp values
        Cp_above_rated = turbine.Cp.interp_surface(0,TSR_above_rated[0])             # Cp during rated operation (not optimal). Assumes cut-in bld pitch to be 0
        Cp_op_br = np.ones(len(v_below_rated)) * turbine.Cp.max              # below rated
        Cp_op_ar = Cp_above_rated * (TSR_above_rated/TSR_rated)**3           # above rated
        Cp_op = np.concatenate((Cp_op_br, Cp_op_ar))                # operational CPs to linearize around
        pitch_initial_rad = turbine.pitch_initial_rad
        TSR_initial = turbine.TSR_initial

        # initialize variables
        pitch_op = np.empty(len(TSR_op))
        dCp_beta = np.empty(len(TSR_op))
        dCp_TSR = np.empty(len(TSR_op))
        # ------------- Find Linearized State Matrices ------------- #

        for i in range(len(TSR_op)):
            # Find pitch angle as a function of expected operating CP for each TSR
            Cp_TSR = np.ndarray.flatten(turbine.Cp.interp_surface(turbine.pitch_initial_rad, TSR_op[i]))     # all Cp values for a given tsr
            Cp_op[i] = np.clip(Cp_op[i], np.min(Cp_TSR), np.max(Cp_TSR))      # saturate Cp values to be on Cp surface
            f_cp_pitch = interpolate.interp1d(Cp_TSR,pitch_initial_rad)        # interpolate function for Cp(tsr) values
            pitch_op[i] = f_cp_pitch(Cp_op[i])      # expected operation blade pitch values
            dCp_beta[i], dCp_TSR[i] = turbine.Cp.interp_gradient(pitch_op[i],TSR_op[i])       # gradients of Cp surface in Beta and TSR directions
        
        # Full Cp surface gradients
        dCp_dbeta = dCp_beta/np.diff(pitch_initial_rad)[0]
        dCp_dTSR = dCp_TSR/np.diff(TSR_initial)[0]
        
        # Linearized system derivatives
        dtau_dbeta = Ng/2*rho*Ar*R*(1/TSR_op)*dCp_dbeta*v**2
        dtau_dlambda = Ng/2*rho*Ar*R*v**2*(1/(TSR_op**2))*(dCp_dTSR*TSR_op - Cp_op)
        dlambda_domega = R/v/Ng
        dtau_domega = dtau_dlambda*dlambda_domega

        # Second order system coefficients
        A = dtau_domega/J             # Plant pole
        B_tau = -Ng**2/J              # Torque input  
        B_beta = dtau_dbeta/J         # Blade pitch input 

        # Wind Disturbance Input
        dlambda_dv = -(TSR_op/v)
        dtau_dv = dtau_dlambda*dlambda_dv
        # B_v = dtau_dv/J # wind speed input - currently unused 


        # separate and define below and above rated parameters
        A_vs = A[0:len(v_below_rated)]          # below rated
        A_pc = A[len(v_below_rated):len(v)]     # above rated
        B_tau = B_tau * np.ones(len(v_below_rated))
        B_beta = B_beta[len(v_below_rated):len(v)]

        # Find gain schedule
        self.pc_gain_schedule = ControllerTypes()
        self.pc_gain_schedule.second_order_PI(self.zeta_pc, self.omega_pc,A_pc,B_beta,linearize=True,v=v_above_rated)
        self.vs_gain_schedule = ControllerTypes()
        self.vs_gain_schedule.second_order_PI(self.zeta_vs, self.omega_vs,A_vs,B_tau,linearize=False,v=v_below_rated)

        # Find K for Komega_g^2
        self.vs_rgn2K = 0.5*rho*Ar*R**5 * turbine.Cp.max / (turbine.Cp.TSR_opt**3 * Ng)
        self.vs_refspd = min(turbine.Cp.TSR_opt * turbine.v_rated/R, turbine.rated_rotor_speed) * Ng

        # Define some setpoints
        self.vs_minspd = (turbine.Cp.TSR_opt * turbine.v_min / turbine.rotor_radius) * Ng

        # Store some variables
        self.v = v                                  # Wind speed (m/s)
        self.v_below_rated = v_below_rated
        self.Cp_op = Cp_op
        self.pitch_op = pitch_op
        self.pitch_op_pc = pitch_op[len(v_below_rated):len(v)]
        self.TSR_op = TSR_op
        self.A = A 
        self.B_beta = B_beta
        self.B_tau = B_tau

        # Peak Shaving
        self.ps = ControllerBlocks()
        self.ps.peak_shaving(self, turbine)

class ControllerBlocks():
    def __init__(self):
        '''
        Controller blocks that need some tuning
        Includes: Peak shaving
        '''
        pass
    
    def peak_shaving(self,controller, turbine):
        ''' 
        Define minimum blade pitch angle for peak shaving routine
        '''

        # Re-define Turbine Parameters for shorthand
        J = turbine.J                           # Total rotor inertial (kg-m^2) 
        rho = turbine.rho                       # Air density (kg/m^3)
        R = turbine.rotor_radius                    # Rotor radius (m)
        A = np.pi*R**2                         # Rotor area (m^2)
        Ng = turbine.Ng                         # Gearbox ratio (-)
        rated_rotor_speed = turbine.rated_rotor_speed               # Rated rotor speed (rad/s)

        # Initialize some arrays
        Ct_op = np.empty(len(controller.TSR_op),dtype='float64')
        Ct_max = np.empty(len(controller.TSR_op),dtype='float64')
        beta_min = np.empty(len(controller.TSR_op),dtype='float64')
        # Find unshaved rotor thurst coefficients and associated rotor thrusts
        # for i in len(controller.TSR_op):
        for i in range(len(controller.TSR_op)):
            Ct_op[i] = turbine.Ct.interp_surface(controller.pitch_op[i],controller.TSR_op[i])
            T = 0.5 * rho * A * controller.v**2 * Ct_op

        # Define minimum max thrust and initialize pitch_min
        Tmax = controller.ps_percent * np.max(T)
        pitch_min = np.ones(len(controller.pitch_op)) * controller.min_pitch

        # Modify pitch_min if max thrust exceeds limits
        for i in range(len(controller.TSR_op)):
            # Find Ct values for operational TSR
            # Ct_tsr = turbine.Ct.interp_surface(turbine.pitch_initial_rad, controller.TSR_op[i])
            Ct_tsr = turbine.Ct.interp_surface(turbine.pitch_initial_rad,controller.TSR_op[i])
            # Define max Ct values
            Ct_max[i] = Tmax/(0.5 * rho * A * controller.v[i]**2)
            if T[i] > Tmax:
                Ct_op[i] = Ct_max[i]
            else:
                Ct_max[i] = np.minimum( np.max(Ct_tsr), Ct_max[i])
            # Define minimum pitch angle
            f_pitch_min = interpolate.interp1d(Ct_tsr, turbine.pitch_initial_rad, bounds_error=False, fill_value=(turbine.pitch_initial_rad[0],turbine.pitch_initial_rad[-1]))
            pitch_min[i] = f_pitch_min(Ct_max[i])

        # save some outputs for analysis or future work
        self.Tshaved = 0.5 * rho * A * controller.v**2 * Ct_op
        self.pitch_min = pitch_min
        self.v = controller.v
        self.Ct_max = Ct_max
        self.Ct_op = Ct_op
        self.T = T

class ControllerTypes():
    def __init__(self):
        '''
        Controller Types class used to define any controllers and their associated
        gains for desired closed loop dynamics
        '''
        pass

    def second_order_PI(self,zeta,om_n,A,B,linearize=False,v=None):

        # Linearize system coefficients w.r.t. wind speed if desired
        if linearize:
            print('Calculating second order PI gain schedule for linearized system pole location.')
            pA = np.polyfit(v,A,1)
            pB = np.polyfit(v,B,1)
            A = pA[0]*v + pA[1]
            B = pB[0]*v + pB[1]

        # Calculate gain schedule
        self.Kp = 1/B * (2*zeta*om_n + A)
        self.Ki = om_n**2/B           

class FileProcessing():
    """
    Class ProcessFile can be used to read in / write out controller parameter files to update
    """

    def __init__(self):
        pass
    def write_param_file(self, param_file, turbine, controller, new_file=True):
        """
        Print the controller parameters to the DISCON.IN input file for the generic controller

        Parameters:
        -----------
            param_file: str
                        filename to for parameter input file
            turbine: class
                     class containing 
            new_file: bool
                      True = create new file, False = modify existing file.  (False functionality not included yet)
        """
        if new_file:
            print('Writing new controller parameter file parameter file %s.' % param_file)
            file = open(param_file,'w')
            file.write('! Controller parameter input file for the %s wind turbine\n' % turbine.TurbineName)
            file.write('!    - File written using NREL Reference Controller tuning logic on %s\n' % now.strftime('%m/%d/%y'))
            file.write('\n')
            file.write('!------- DEBUG ------------------------------------------------------------\n')
            file.write('1					! LoggingLevel		- {0: write no debug files, 1: write standard output .dbg-file, 2: write standard output .dbg-file and complete avrSWAP-array .dbg2-file\n')
            file.write('\n')
            file.write('!------- CONTROLLER FLAGS -------------------------------------------------\n')
            file.write('1					! F_LPFType			- {1: first-order low-pass filter, 2: second-order low-pass filter}, [rad/s] (currently filters generator speed and pitch control signals)\n')
            file.write('0					! F_NotchType		- Notch on the measured generator speed {0: disable, 1: enable} \n')
            file.write('0					! IPC_ControlMode	- Turn Individual Pitch Control (IPC) for fatigue load reductions (pitch contribution) {0: off, 1: 1P reductions, 2: 1P+2P reductions}\n')
            file.write('2					! VS_ControlMode	- Generator torque control mode in above rated conditions {0: constant torque, 1: constant power, 2: TSR tracking PI control}\n')
            file.write('1                   ! PC_ControlMode    - Blade pitch control mode {0: No pitch, fix to fine pitch, 1: active PI blade pitch control}\n')
            file.write('0					! Y_ControlMode		- Yaw control mode {0: no yaw control, 1: yaw rate control, 2: yaw-by-IPC}\n')
            file.write('1                   ! SS_Mode           - Setpoint Smoother mode {0: no setpoint smoothing, 1: introduce setpoint smoothing}\n')
            file.write('0                   ! WE_Mode           - Wind speed estimator mode {0: One-second low pass filtered hub height wind speed, 1: Imersion and Invariance Estimator (Ortega et al.)}\n')
            file.write('0                   ! PS_Mode           - Peak shaving mode {0: no peak shaving, 1: implement peak shaving}\n')
            file.write('\n')
            file.write('!------- FILTERS ----------------------------------------------------------\n') 
            file.write('{:<12.11}        ! F_LPFCornerFreq	- Corner frequency (-3dB point) in the low-pass filters, [rad/s]\n'.format(turbine.bld_edgewise_freq * 1/4)) 
            file.write('0                   ! F_LPFDamping		- Damping coefficient [used only when F_FilterType = 2]\n')
            file.write('0					! F_NotchCornerFreq	- Natural frequency of the notch filter, [rad/s]\n')
            file.write('0	0				! F_NotchBetaNumDen	- Two notch damping values (numerator and denominator, resp) - determines the width and depth of the notch, [-]\n')
            file.write('{:<12.10}        ! F_SSCornerFreq    - Corner frequency (-3dB point) in the first order low pass filter for the setpoint smoother, [rad/s].\n'.format(controller.ss_cornerfreq))
            file.write('\n')
            file.write('!------- BLADE PITCH CONTROL ----------------------------------------------\n')
            file.write('{}              ! PC_GS_n			- Amount of gain-scheduling table entries\n'.format(format(len(controller.pitch_op_pc), '<6d')))
            file.write('{}              ! PC_GS_angles	    - Gain-schedule table: pitch angles\n'.format(str(controller.pitch_op_pc).strip('[]').replace('\n',''))) 
            file.write('{}              ! PC_GS_KP		- Gain-schedule table: pitch controller kp gains\n'.format(str(controller.pc_gain_schedule.Kp).strip('[]').replace('\n','')))
            file.write('{}              ! PC_GS_KI		- Gain-schedule table: pitch controller ki gains\n'.format(str(controller.pc_gain_schedule.Ki).strip('[]').replace('\n','')))
            file.write('{}              ! PC_GS_KD			- Gain-schedule table: pitch controller kd gains\n'.format(str(np.zeros(len(controller.pitch_op_pc))).strip('[]').replace('\n','')))
            file.write('{}              ! PC_GS_TF			- Gain-schedule table: pitch controller tf gains (derivative filter)\n'.format(str(np.zeros(len(controller.pitch_op_pc))).strip('[]').replace('\n','')))
            file.write('{:<12.11}        ! PC_MaxPit			- Maximum physical pitch limit, [rad].\n'.format(controller.max_pitch))
            file.write('{:<12.11}        ! PC_MinPit			- Minimum physical pitch limit, [rad].\n'.format(controller.min_pitch))
            file.write('{:<12.11}	    ! PC_MaxRat			- Maximum pitch rate (in absolute value) in pitch controller, [rad/s].\n'.format(turbine.max_pitch_rate))
            file.write('{:<12.11}	    ! PC_MinRat			- Minimum pitch rate (in absolute value) in pitch controller, [rad/s].\n'.format(-turbine.max_pitch_rate))
            file.write('{:<12.11}        ! PC_RefSpd			- Desired (reference) HSS speed for pitch controller, [rad/s].\n'.format(turbine.rated_rotor_speed*turbine.Ng))
            file.write('{:<12.11}        ! PC_FinePit		- Record 5: Below-rated pitch angle set-point, [rad]\n'.format(controller.min_pitch))
            file.write('0.003490658			! PC_Switch			- Angle above lowest minimum pitch angle for switch, [rad]\n')
            file.write('0					! Z_EnableSine		- Enable/disable sine pitch excitation, used to validate for dynamic induction control, will be removed later, [-]\n')
            file.write('0.0349066			! Z_PitchAmplitude	- Amplitude of sine pitch excitation, [rad]\n')
            file.write('0					! Z_PitchFrequency	- Frequency of sine pitch excitation, [rad/s]\n')
            file.write('\n')
            file.write('!------- INDIVIDUAL PITCH CONTROL -----------------------------------------\n')
            file.write('0.0			        ! IPC_IntSat		- Integrator saturation (maximum signal amplitude contribution to pitch from IPC), [rad]\n')
            file.write('0.0 0.0				! IPC_KI			- Integral gain for the individual pitch controller: first parameter for 1P reductions, second for 2P reductions, [-]\n')
            file.write('0.0	0.0		        ! IPC_aziOffset		- Phase offset added to the azimuth angle for the individual pitch controller, [rad]. \n')
            file.write('0.0					! IPC_CornerFreqAct - Corner frequency of the first-order actuators model, to induce a phase lag in the IPC signal {0: Disable}, [rad/s]\n')
            file.write('\n')
            file.write('!------- VS TORQUE CONTROL ------------------------------------------------\n')
            file.write('{:<12.11}        ! VS_GenEff			- Generator efficiency mechanical power -> electrical power, [should match the efficiency defined in the generator properties!], [-]\n'.format(turbine.GenEff))
            file.write('{:<12.11}        ! VS_ArSatTq		- Above rated generator torque PI control saturation, [Nm]\n'.format(turbine.rated_torque))
            file.write('{:<12.11}        ! VS_MaxRat			- Maximum torque rate (in absolute value) in torque controller, [Nm/s].\n'.format(turbine.max_torque_rate))
            file.write('{:<12.11}        ! VS_MaxTq			- Maximum generator torque in Region 3 (HSS side), [Nm].\n'.format(turbine.rated_torque*1.1))
            file.write('0.0					! VS_MinTq			- Minimum generator (HSS side), [Nm].\n')
            file.write('{:<12.11}        ! VS_MinOMSpd		- Optimal mode minimum speed, cut-in speed towards optimal mode gain path, [rad/s]\n'.format(controller.vs_minspd))
            file.write('{:<12.11}        ! VS_Rgn2K			- Generator torque constant in Region 2 (HSS side), [N-m/(rad/s)^2]\n'.format(controller.vs_rgn2K))
            file.write('{:<12.11}        ! VS_RtPwr			- Wind turbine rated power [W]\n'.format(turbine.rated_power))
            file.write('{:<12.11}        ! VS_RtTq			- Rated torque, [Nm].\n'.format(turbine.rated_torque))
            file.write('{:<12.11}        ! VS_RefSpd			- Rated generator speed [rad/s]\n'.format(controller.vs_refspd))
            file.write('1					! VS_n				- Number of generator PI torque controller gains\n')
            file.write('{:<12.11}       ! VS_KP				- Proportional gain for generator PI torque controller [1/(rad/s) Nm]. (Only used in the transitional 2.5 region if VS_ControlMode =/ 2)\n'.format(controller.vs_gain_schedule.Kp[-1]))
            file.write('{:<12.11}       ! VS_KI				- Integral gain for generator PI torque controller [1/rad Nm]. (Only used in the transitional 2.5 region if VS_ControlMode =/ 2)\n'.format(controller.vs_gain_schedule.Ki[-1]))
            file.write('{:<12.11}        ! VS_TSRopt			- Power-maximizing region 2 tip-speed-ratio [rad].\n'.format(turbine.Cp.TSR_opt))
            file.write('\n')
            file.write('!------- SETPOINT SMOOTHER ---------------------------------------------\n')
            file.write('{:<12.11}        ! SS_VSGain         - Variable speed torque controller setpoint smoother gain, [-].\n'.format(controller.ss_vsgain))
            file.write('{:<12.11}        ! SS_PCGain         - Collective pitch controller setpoint smoother gain, [-].\n'.format(controller.ss_pcgain))
            file.write('\n')
            file.write('!------- WIND SPEED ESTIMATOR ---------------------------------------------\n')
            file.write('{:<12.11}        ! WE_BladeRadius	- Blade length [m]\n'.format(turbine.rotor_radius))
            file.write('4					! WE_CP_n			- Amount of parameters in the Cp array\n')
            file.write('0.0 0.0 0.0 0.0	    ! WE_CP - Parameters that define the parameterized CP(lambda) function\n')
            file.write('0.0					! WE_Gamma			- Adaption gain of the wind speed estimator algorithm [m/rad]\n')
            file.write('{:<12.12}        ! WE_GearboxRatio	- Gearbox ratio [>=1],  [-]\n'.format(turbine.Ng))
            file.write('{:<12.12}        ! WE_Jtot			- Total drivetrain inertia, including blades, hub and casted generator inertia to LSS, [kg m^2]\n'.format(turbine.J))
            file.write('1.225				! WE_RhoAir			- Air density, [kg m^-3]\n')
            file.write('"Cp_Ct_Cq.txt"      ! PerfFileName      - File containing rotor performance tables (Cp,Ct,Cq)\n')
            file.write('{} {}           ! PerfTableSize     - Size of rotor performance tables, first number refers to number of blade pitch angles, second number referse to number of tip-speed ratios\n'.format(format(len(turbine.Cp.pitch_initial_rad),'<4d'),format(len(turbine.Cp.TSR_initial),'<4d')))
            file.write('{}              ! WE_FOPoles_N      - Number of first-order system poles used in EKF\n'.format(format(len(controller.A),'<6d')))
            file.write('{}              ! WE_FOPoles_v      - Wind speeds corresponding to first-order system poles [m/s]\n'.format(str(controller.v).strip('[]').replace('\n','')))
            file.write('{}              ! WE_FOPoles        - First order system poles\n'.format(str(controller.A).strip('[]').replace('\n','')))
            file.write('\n')
            file.write('!------- YAW CONTROL ------------------------------------------------------\n')
            file.write('0.0			        ! Y_ErrThresh		- Yaw error threshold. Turbine begins to yaw when it passes this. [rad^2 s]\n')
            file.write('0.0				    ! Y_IPC_IntSat		- Integrator saturation (maximum signal amplitude contribution to pitch from yaw-by-IPC), [rad]\n')
            file.write('1					! Y_IPC_n			- Number of controller gains (yaw-by-IPC)\n')
            file.write('0.0				    ! Y_IPC_KP			- Yaw-by-IPC proportional controller gain Kp\n')
            file.write('0.0				    ! Y_IPC_KI			- Yaw-by-IPC integral controller gain Ki\n')
            file.write('0.0			        ! Y_IPC_omegaLP		- Low-pass filter corner frequency for the Yaw-by-IPC controller to filtering the yaw alignment error, [rad/s].\n')
            file.write('0.0					! Y_IPC_zetaLP		- Low-pass filter damping factor for the Yaw-by-IPC controller to filtering the yaw alignment error, [-].\n')
            file.write('0.0			        ! Y_MErrSet			- Yaw alignment error, set point [rad]\n')
            file.write('0.0					! Y_omegaLPFast		- Corner frequency fast low pass filter, 1.0 [Hz]\n')
            file.write('0.0			        ! Y_omegaLPSlow		- Corner frequency slow low pass filter, 1/60 [Hz]\n')
            file.write('0.0			        ! Y_Rate			- Yaw rate [rad/s]\n')
            file.write('\n')
            file.write('!------- TOWER FORE-AFT DAMPING -------------------------------------------\n')
            file.write('-1					! FA_KI				- Integral gain for the fore-aft tower damper controller, -1 = off / >0 = on [rad s/m] - !NJA - Make this a flag\n')
            file.write('0.0                 ! FA_HPF_CornerFreq	- Corner frequency (-3dB point) in the high-pass filter on the fore-aft acceleration signal [rad/s]\n')
            file.write('0.0			        ! FA_IntSat			- Integrator saturation (maximum signal amplitude contribution to pitch from FA damper), [rad]\n')
            file.write('\n')
            file.write('!------- PEAK SHAVING -------------------------------------------\n')
            file.write('{}              ! PS_BldPitchMin_N  - Number of values in minimum blade pitch lookup table (should equal number of values in PS_WindSpeeds and PS_BldPitchMin)\n'.format(format(len(controller.ps.pitch_min),'<6d')))
            file.write('{}              ! PS_WindSpeeds       - Wind speeds corresponding to minimum blade pitch angles [m/s]\n'.format(str(controller.ps.v).strip('[]').replace('\n','')))
            file.write('{}              ! PS_BldPitchMin          - Minimum blade pitch angles [rad]\n'.format(str(controller.ps.pitch_min).strip('[]').replace('\n','')))
            file.close()
