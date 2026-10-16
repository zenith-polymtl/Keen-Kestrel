"""Lance un monde généré par worlds/build_world.py avec l'iris ArduPilot (SITL + Gazebo + RViz).

Calqué sur ardupilot_gz_bringup/iris_runway.launch.py, avec le monde en argument. Le home du
SITL n'est pas passé ici (robot.launch.py ne transmet pas --home) : il vient des SIM_OPOS_*
du fichier <site>.parm, que le Makefile place dans la chaîne `defaults`.

    ros2 launch /ardu_ws/worlds/world.launch.py site:=medicine_hat defaults:=...
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

WORLDS = os.path.dirname(os.path.realpath(__file__))


def setup(context):
    site = LaunchConfiguration("site").perform(context)
    world_file = LaunchConfiguration("world_file").perform(context)
    sdf = os.path.join(WORLDS, site, "build", f"{world_file}.sdf")
    if not os.path.exists(sdf):
        raise RuntimeError(f"{sdf} introuvable : lancer `make world WORLD={site}` sur l'hôte")

    gz_launch = os.path.join(get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")
    bringup = get_package_share_directory("ardupilot_gz_bringup")
    server = IncludeLaunchDescription(PythonLaunchDescriptionSource(gz_launch),
                                      launch_arguments={"gz_args": f"-v4 -s -r {sdf}"}.items())
    gui = IncludeLaunchDescription(PythonLaunchDescriptionSource(gz_launch),
                                   launch_arguments={"gz_args": "-v4 -g"}.items(),
                                   condition=IfCondition(LaunchConfiguration("gui")))
    # Nom du monde = nom du site : le pont ros_gz et le spawn adressent /world/<site>/...
    robot = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(bringup, "launch", "robots", "iris.launch.py")),
        launch_arguments={"world_name": site, "defaults": LaunchConfiguration("defaults"),
                          "z": LaunchConfiguration("z")}.items())
    rviz = Node(package="rviz2", executable="rviz2",
                arguments=["-d", os.path.join(bringup, "rviz", "iris.rviz")],
                condition=IfCondition(LaunchConfiguration("rviz")))
    return [server, gui, robot, rviz]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("site", description="Dossier du site dans worlds/ (ex. medicine_hat)."),
        DeclareLaunchArgument("world_file", default_value="world",
                              description="world (avec repères de limites) ou world_clean (sans)."),
        DeclareLaunchArgument("defaults", description="Chaîne --defaults du SITL, <site>.parm compris."),
        DeclareLaunchArgument("z", default_value="0.2", description="Hauteur d'apparition : le sol vaut 0 à l'origine."),
        DeclareLaunchArgument("gui", default_value="true", description="Ouvrir l'interface Gazebo."),
        DeclareLaunchArgument("rviz", default_value="true", description="Ouvrir RViz."),
        OpaqueFunction(function=setup),
    ])
