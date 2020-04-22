from collections import OrderedDict
import numpy as np
from copy import deepcopy

from robosuite.utils.mjcf_utils import bounds_to_grid
from robosuite.utils.transform_utils import convert_quat
import robosuite.utils.env_utils as EU
from robosuite.environments.sawyer import SawyerEnv

from robosuite.models.arenas import DoorArena
from robosuite.models.objects import DoorObject
from robosuite.models.robots import Sawyer
from robosuite.models.tasks import TableTopMergedTask, DoorTask, UniformRandomSampler, RoundRobinSampler
from robosuite.controllers import load_controller_config
import os


class SawyerDoor(SawyerEnv):
    """
    This class corresponds to the lifting task for the Sawyer robot arm.
    """

    def __init__(
        self,
        controller_config=None,
        gripper_type="TwoFingerGripper",
        table_full_size=(0.8, 0.8, 0.8),
        table_friction=(1., 5e-3, 1e-4),
        use_camera_obs=True,
        use_object_obs=True,
        reward_shaping=False,
        placement_initializer=None,
        gripper_visualization=False,
        use_indicator_object=False,
        indicator_num=1,
        has_renderer=False,
        has_offscreen_renderer=True,
        render_collision_mesh=False,
        render_visual_mesh=True,
        control_freq=10,
        horizon=1000,
        ignore_done=False,
        camera_name="frontview",
        camera_height=256,
        camera_width=256,
        camera_depth=False,
        camera_real_depth=False,
        camera_segmentation=False,
        eval_mode=False,
        perturb_evals=False,
    ):
        """
        Args:
            controller_config (dict): If set, contains relevant controller parameters for creating a custom controller.
                Else, uses the default controller for this specific task

            gripper_type (str): type of gripper, used to instantiate
                gripper models from gripper factory.

            table_full_size (3-tuple): x, y, and z dimensions of the table.

            table_friction (3-tuple): the three mujoco friction parameters for
                the table.

            use_camera_obs (bool): if True, every observation includes a
                rendered image.

            use_object_obs (bool): if True, include object (cube) information in
                the observation.

            reward_shaping (bool): if True, use dense rewards.

            placement_initializer (ObjectPositionSampler instance): if provided, will
                be used to place objects on every reset, else a UniformRandomSampler
                is used by default.

            gripper_visualization (bool): True if using gripper visualization.
                Useful for teleoperation.

            use_indicator_object (bool): if True, sets up an indicator object that
                is useful for debugging.

            indicator_num (int): number of indicator objects to add to the environment.
                Only used if @use_indicator_object is True.

            has_renderer (bool): If true, render the simulation state in
                a viewer instead of headless mode.

            has_offscreen_renderer (bool): True if using off-screen rendering.

            render_collision_mesh (bool): True if rendering collision meshes
                in camera. False otherwise.

            render_visual_mesh (bool): True if rendering visual meshes
                in camera. False otherwise.

            control_freq (float): how many control signals to receive
                in every second. This sets the amount of simulation time
                that passes between every action input.

            horizon (int): Every episode lasts for exactly @horizon timesteps.

            ignore_done (bool): True if never terminating the environment (ignore @horizon).

            camera_name (str): name of camera to be rendered. Must be
                set if @use_camera_obs is True.

            camera_height (int): height of camera frame.

            camera_width (int): width of camera frame.

            camera_depth (bool): True if rendering RGB-D, and RGB otherwise.

            camera_real_depth (bool): True if convert depth to real depth in meters

            camera_segmentation (bool): True if also return semantic segmentation of the camera view
        """

        # Load the default controller if none is specified
        if controller_config is None:
            controller_path = os.path.join(os.path.dirname(__file__), '..', 'controllers/config/default_sawyer.json')
            controller_config = load_controller_config(custom_fpath=controller_path)

        # Assert that the controller config is a dict file
        assert type(controller_config) == dict, \
            "Inputted controller config must be a dict! Instead, got type: {}".format(type(controller_config))

        # settings for table top
        self.table_full_size = table_full_size
        self.table_friction = table_friction

        # whether to use ground-truth object states
        self.use_object_obs = use_object_obs

        # reward configuration
        self.reward_shaping = reward_shaping

        # object placement initializer
        if placement_initializer is not None:
            self.placement_initializer = placement_initializer
        else:
            self.placement_initializer = UniformRandomSampler(
                x_range=[0.1, 0.1],
                y_range=[-0.35, -0.35],
                ensure_object_boundary_in_range=False,
                # z_rotation=None,
                z_rotation=(-np.pi / 2.),
                z_offset=0.02,
            )

        super().__init__(
            controller_config=controller_config,
            gripper_type=gripper_type,
            gripper_visualization=gripper_visualization,
            use_indicator_object=use_indicator_object,
            indicator_num=indicator_num,
            has_renderer=has_renderer,
            has_offscreen_renderer=has_offscreen_renderer,
            render_collision_mesh=render_collision_mesh,
            render_visual_mesh=render_visual_mesh,
            control_freq=control_freq,
            horizon=horizon,
            ignore_done=ignore_done,
            use_camera_obs=use_camera_obs,
            camera_name=camera_name,
            camera_height=camera_height,
            camera_width=camera_width,
            camera_depth=camera_depth,
            camera_real_depth=camera_real_depth,
            camera_segmentation=camera_segmentation,
            eval_mode=eval_mode,
            perturb_evals=perturb_evals,
        )

    def _get_placement_initializer_for_eval_mode(self):
        """
        Sets a placement initializer that is used to initialize the
        environment into a fixed set of known task instances.
        This is for reproducibility in policy evaluation.
        """
        assert(self.eval_mode)

        bounds = list(self._grid_bounds_for_eval_mode())
        if self.perturb_evals:
            # perturbation sizes should be half the grid spacing
            perturb_sizes = [((b[1] - b[0]) / b[2]) / 2. for b in bounds]
        else:
            perturb_sizes = [None for b in bounds]

        object_grid = bounds_to_grid(bounds)
        self.placement_initializer  = RoundRobinSampler(
            x_range=object_grid[0],
            y_range=object_grid[1],
            ensure_object_boundary_in_range=False,
            z_rotation=object_grid[2],
            x_perturb=perturb_sizes[0],
            y_perturb=perturb_sizes[1],
            z_rotation_perturb=perturb_sizes[2],
            z_offset=0.02,
        )

    def _grid_bounds_for_eval_mode(self):
        """
        Helper function to get grid bounds of x positions, y positions, 
        and z-rotations for reproducible evaluations, and number of points
        per dimension.
        """

        # (low, high, number of grid points for this dimension)
        x_bounds = (0.1, 0.1, 1)
        y_bounds = (-0.35, -0.35, 1)
        z_rot_bounds = (-np.pi / 2., -np.pi / 2., 1)
        return x_bounds, y_bounds, z_rot_bounds

    def _load_model(self):
        """
        Loads an xml model, puts it in self.model
        """
        super()._load_model()
        self.mujoco_robot.set_base_xpos([0, 0, 0])

        # load model for table top workspace
        self.mujoco_arena = DoorArena(
            table_full_size=self.table_full_size, table_friction=self.table_friction
        )
        if self.use_indicator_object:
            self.mujoco_arena.add_pos_indicator(self.indicator_num)

        # The sawyer robot has a pedestal, we want to align it with the table
        self.mujoco_arena.set_origin([0.16 + self.table_full_size[0] / 2, 0, 0])

        # initialize objects of interest
        door = DoorObject(
            joint=[],
            # friction=None,
            # damping=None,
            friction=0.0,
            damping=0.1,
            lock=True,
        )
        self.mujoco_objects = OrderedDict([("Door", door)])

        # reset initial joint positions (gets reset in sim during super() call in _reset_internal)
        self.init_qpos = np.array([-0.5538, -0.8208, 0.4155, 1.8409, -0.4955, 0.6482, 1.9628])
        self.init_qpos += np.random.randn(self.init_qpos.shape[0]) * 0.02

        # task includes arena, robot, and objects of interest
        self.model = TableTopMergedTask(
            self.mujoco_arena,
            self.mujoco_robot,
            self.mujoco_objects,
            initializer=self.placement_initializer,
        )
        self.model.place_objects()

    def _get_reference(self):
        """
        Sets up references to important components. A reference is typically an
        index or a list of indices that point to the corresponding elements
        in a flatten array, which is how MuJoCo stores physical simulation data.
        """
        super()._get_reference()
        self.object_body_ids = {}
        self.object_body_ids["door"]  = self.sim.model.body_name2id("door")
        self.object_body_ids["frame"] = self.sim.model.body_name2id("frame")
        self.object_body_ids["latch"]  = self.sim.model.body_name2id("latch")
        self.door_handle_site_id = self.sim.model.site_name2id("door_handle")
        self.hinge_qpos_addr = self.sim.model.get_joint_qpos_addr("door_hinge")
        self.handle_qpos_addr = self.sim.model.get_joint_qpos_addr("latch_joint")

        self.l_finger_geom_ids = [
            self.sim.model.geom_name2id(x) for x in self.gripper.left_finger_geoms
        ]
        self.r_finger_geom_ids = [
            self.sim.model.geom_name2id(x) for x in self.gripper.right_finger_geoms
        ]

    def _reset_internal(self):
        """
        Resets simulation internal configurations.
        """
        super()._reset_internal()

    def reward(self, action=None):
        """
        Reward function for the task.

        The dense reward has three components.

            Reaching: in [0, 1], to encourage the arm to reach the cube
            Grasping: in {0, 0.25}, non-zero if arm is grasping the cube
            Lifting: in {0, 1}, non-zero if arm has lifted the cube

        The sparse reward only consists of the lifting component.

        Args:
            action (np array): unused for this task

        Returns:
            reward (float): the reward
        """
        reward = 0.
        if self._check_success():
            reward = 1.
        return reward

    def _get_observation(self):
        """
        Returns an OrderedDict containing observations [(name_string, np.array), ...].

        Important keys:
            robot-state: contains robot-centric information.
            object-state: requires @self.use_object_obs to be True.
                contains object-centric information.
            image: requires @self.use_camera_obs to be True.
                contains a rendered frame from the simulation.
            depth: requires @self.use_camera_obs and @self.camera_depth to be True.
                contains a rendered depth map from the simulation
        """
        di = super()._get_observation()
        if self.use_object_obs:
            eef_pos = np.array(self.sim.data.site_xpos[self.eef_site_id])
            door_pos = np.array(self.sim.data.body_xpos[self.object_body_ids["door"]])
            # frame_pos = np.array(self.sim.data.body_xpos[self.object_body_ids["frame"]])
            # latch_pos = np.array(self.sim.data.body_xpos[self.object_body_ids["latch"]])
            handle_pos = np.array(self.sim.data.site_xpos[self.door_handle_site_id])
            hinge_qpos = np.array([self.sim.data.qpos[self.hinge_qpos_addr]])
            handle_qpos = np.array([self.sim.data.qpos[self.handle_qpos_addr]])

            di["door_pos"] = door_pos
            di["handle_pos"] = handle_pos
            di["door_to_eef_pos"] = door_pos - eef_pos
            di["handle_to_eef_pos"] = handle_pos - eef_pos
            di["hinge_qpos"] = hinge_qpos
            di["handle_qpos"] = handle_qpos
        
            di['object-state'] = np.concatenate([
                di["door_pos"],
                di["handle_pos"],
                di["door_to_eef_pos"],
                di["handle_to_eef_pos"],
                di["hinge_qpos"],
                di["handle_qpos"],
            ])

            # print(np.linalg.norm(eef_pos - handle_pos))

        return di

    def _check_contact(self):
        """
        Returns True if gripper is in contact with an object.
        """
        collision = False
        for contact in self.sim.data.contact[: self.sim.data.ncon]:
            if (
                self.sim.model.geom_id2name(contact.geom1)
                in self.gripper.contact_geoms()
                or self.sim.model.geom_id2name(contact.geom2)
                in self.gripper.contact_geoms()
            ):
                collision = True
                break
        return collision

    def _check_success(self):
        """
        Returns True if door has been opened.
        """
        hinge_qpos = self.sim.data.qpos[self.hinge_qpos_addr]
        return (hinge_qpos > 0.3)

    def _gripper_visualization(self):
        """
        Do any needed visualization here. Overrides superclass implementations.
        """

        # color the gripper site appropriately based on distance to door handle
        if self.gripper_visualization:
            # get distance to door handle
            dist = np.sum(
                np.square(
                    self.sim.data.site_xpos[self.door_handle_site_id]
                    - self.sim.data.get_site_xpos("grip_site")
                )
            )

            # set RGBA for the EEF site here
            max_dist = 0.1
            scaled = (1.0 - min(dist / max_dist, 1.)) ** 15
            rgba = np.zeros(4)
            rgba[0] = 1 - scaled
            rgba[1] = scaled
            rgba[3] = 0.5

            self.sim.model.site_rgba[self.eef_site_id] = rgba
