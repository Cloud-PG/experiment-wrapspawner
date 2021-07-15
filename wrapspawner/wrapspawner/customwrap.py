import json
import os
import re
import urllib.request
from types import coroutine

import nest_asyncio
from jupyterhub.spawner import LocalProcessSpawner, Spawner
from rich import inspect
from tornado import concurrent, gen
from tornado.ioloop import IOLoop
from traitlets import (
    Any,
    Dict,
    Float,
    Instance,
    Integer,
    List,
    Tuple,
    Type,
    Unicode,
    directional_link,
)

# Only needed for DockerProfilesSpawner
try:
    import docker
except ImportError:
    pass

from .wrapspawner import WrapSpawner


class CustomDockerProfilesSpawner(WrapSpawner):

    """CustomDockerProfilesSpawner - Example of a custom wrap spawner with
    additional inputs from the user, personalized in base of user's groups.
    """

    # The user's groups list, populated by the jupyterhub spawner hook
    # NOTE: see jupyterhub_config.py -> c.Authenticator.post_auth_hook
    groups = List()

    # Profiles is a list of configuration with different Spawners
    # NOTE: see the property profile to understand how it is populated
    profiles = List(
        trait=Tuple(Unicode(), Unicode(), Type(Spawner), Dict()),
        default_value=[
            (
                "Local Notebook Server",
                "local",
                LocalProcessSpawner,
                {"start_timeout": 15, "http_timeout": 10},
            )
        ],
        minlen=1,
        config=True,
        help="""List of profiles to offer for selection. Signature is:
            List(Tuple( Unicode, Unicode, Type(Spawner), Dict )) corresponding to
            profile display name, unique key, Spawner class, dictionary of spawner config options.

            The first three values will be exposed in the input_template as {display}, {key}, and {type}""",
    )

    # The profile selected by the user and used to construct the target spawner
    child_profile = Unicode()

    # Value to populate the first selection of a list
    first_template = Unicode(
        "selected", config=True, help="Text to substitute as {first} in input_template"
    )

    # The text that compose the inner part of the form
    # NOTE: see the property to understande how it is composed. This is what
    # the user will see in the HTML form
    options_form = Unicode()

    # The form inputs presented to the user
    form_template = Unicode(
        """<label for="profile">Select a job profile:</label>
        <select class="form-control" name="profile" required autofocus>
        {input_template}
        </select>
        
        <label for="profile">Select docker image:</label>
        <select class="form-control" name="dockerImage" required autofocus>
        {input_image_template}
        </select>
        """,
        config=True,
        help="""Template to use to construct options_form text. {input_template} is replaced with
            the result of formatting input_template against each item in the profiles list.""",
    )

    # Input template for the list of images
    input_image_template = Unicode(
        """
        <option value="{key}" {first}>{display}</option>""",
        config=True,
        help="""Template to construct {input_image_template} in form_template.""",
    )

    # input template for the list of profiles
    input_template = Unicode(
        """
        <option value="{key}" {first}>{display}</option>""",
        config=True,
        help="""Template to construct {input_template} in form_template. This text will be formatted
            against each item in the profiles list, in order, using the following key names:
            ( display, key, type ) for the first three items in the tuple, and additionally
            first = "checked" (taken from first_template) for the first item in the list, so that
            the first item starts selected.""",
    )

    # Example profiles
    default_profiles = List(
        trait=Tuple(Unicode(), Unicode(), Type(Spawner), Dict()),
        default_value=[
            (
                "Run Docker Image",
                "singleuser",
                "dockerspawner.SystemUserSpawner",
                {
                    # "image": "jupyterhub/singleuser",
                    "image": "sbrambla",
                },
            ),
        ],
        config=True,
        help="""List of profiles to offer in addition to docker images for selection. Signature is:
            List(Tuple( Unicode, Unicode, Type(Spawner), Dict )) corresponding to
            profile display name, unique key, Spawner class, dictionary of spawner config options.

            The first three values will be exposed in the input_template as {display}, {key}, and {type}""",
    )

    # The selected image
    profile_image = Unicode()

    # Example images for dummy groups
    group_images = {
        "group_a": [
            (
                "base image group_a",
                "jupyterhub/singleuser",
            )
        ],
        "group_b": [
            (
                "base image group_b",
                "jupyterhub/singleuser",
            ),
        ],
    }

    default_profile_image = List(
        trait=Tuple(Unicode(), Unicode()),
        default_value=[
            (
                "base",
                "jupyterhub/singleuser",
            ),
        ],
        config=True,
    )

    docker_spawner_args = Dict(
        default_value={}, config=True, help="Args to pass to DockerSpawner."
    )

    jupyterhub_docker_tag_re = re.compile("^.*jupyterhub$")

    def options_from_form(self, formdata):
        # Default to first profile if somehow none is provided
        return dict(
            profile=formdata.get("profile", [self.profiles[0][1]])[0],
            dockerImage=formdata.get("dockerImage", [self.images[0][1]])[0],
        )

    def select_profile(self, profile, image):
        # Select matching profile, or do nothing (leaving previous or default config in place)
        for p in self.profiles:
            if p[1] == profile:
                self.child_class = p[2]
                self.child_config = p[3]
                # Insert the specific image to child_config
                # NOTE: child_config will contains all the additional parameters
                # of the selected spawner
                self.child_config["image"] = image

                inspect(self.child_config)

                break

    def construct_child(self):
        self.child_profile = self.user_options.get("profile", "")
        self.profile_image = self.user_options.get("dockerImage", "")
        self.select_profile(self.child_profile, self.profile_image)
        super().construct_child()  # this is where the wrapper take effect

    def load_child_class(self, state):
        try:
            self.child_profile = state["profile"]
            self.profile_image = state["dockerImage"]
        except KeyError:
            self.child_profile = ""
            self.profile_image = ""
        self.select_profile(self.child_profile, self.profile_image)

    def get_state(self):
        state = super().get_state()
        state["profile"] = self.child_profile
        state["dockerImage"] = self.profile_image
        return state

    def clear_state(self):
        super().clear_state()
        self.child_profile = ""
        self.profile_image = ""

    def _nvidia_args(self):
        try:
            resp = urllib.request.urlopen("http://localhost:3476/v1.0/docker/cli/json")
            body = resp.read().decode("utf-8")
            args = json.loads(body)
            return dict(
                read_only_volumes={
                    vol.split(":")[0]: vol.split(":")[1] for vol in args["Volumes"]
                },
                extra_create_kwargs={"volume_driver": args["VolumeDriver"]},
                extra_host_config={"devices": args["Devices"]},
            )
        except urllib.error.URLError:
            return {}

    def _docker_profile(self, nvidia_args, image):
        spawner_args = dict(container_image=image, network_name=self.user.name)
        spawner_args.update(self.docker_spawner_args)
        spawner_args.update(nvidia_args)
        nvidia_enabled = "w/GPU" if len(nvidia_args) > 0 else "no GPU"
        return (
            "Docker: (%s): %s" % (nvidia_enabled, image),
            "docker-%s" % (image),
            "dockerspawner.SystemUserSpawner",
            spawner_args,
        )

    def _jupyterhub_docker_tags(self):
        try:
            include_jh_tags = lambda tag: self.jupyterhub_docker_tag_re.match(tag)
            return filter(
                include_jh_tags,
                [
                    tag
                    for image in docker.from_env().images.list()
                    for tag in image.tags
                ],
            )
        except NameError:
            raise Exception(
                "The docker package is not installed and is a dependency for DockerProfilesSpawner"
            )

    def _docker_profiles(self):
        return [
            self._docker_profile(self._nvidia_args(), tag)
            for tag in self._jupyterhub_docker_tags()
        ]

    @property
    def profiles(self):
        return self.default_profiles + self._docker_profiles()

    def images(self, group: str = "") -> Any:
        if group:
            return self.group_images.get(group, [])
        return self.default_profile_image

    @property
    def options_form(self):
        # --------------------- If using nested coroutines ---------------------
        # Used to call get_auth_state coroutine inside the tornado IOLoop (asyncio wrapper)
        # nest_asyncio.apply()

        # Get the IOLoop and run the get_auth_state coroutine
        # io_loop = IOLoop.current()
        # auth_state = io_loop.run_sync(self.user.get_auth_state)
        # inspect(auth_state)
        # self.groups = auth_state.get("groups", [])

        inspect(self.groups)
        inspect(self.admin_access)

        # Profiles
        temp_keys = [
            dict(display=p[0], key=p[1], type=p[2], first="") for p in self.profiles
        ]
        temp_keys[0]["first"] = self.first_template
        # Images
        temp_images = []
        for group in self.groups:
            temp_images += [
                dict(display=p[0], key=p[1], first="") for p in self.images(group)
            ]

        if temp_images:
            temp_images[0]["first"] = self.first_template

        text = "".join([self.input_template.format(**tk) for tk in temp_keys])
        textImages = "".join(
            [self.input_image_template.format(**tk) for tk in temp_images]
        )
        return self.form_template.format(
            input_template=text, input_image_template=textImages
        )
