# Copyright (C) SchedMD LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

###########
# GENERAL #
###########

project_id = "<PROJECT_ID>"
zone       = "us-central1-a"

prefix = "schedmd"

#########
# IMAGE #
#########

# NOTE: Your Project ID will be automatically appended
source_image_project_id = "cloud-hpc-image-public"

#source_image        = null
source_image_family = "hpc-rocky-linux-8"

# *NOT* intended for production use
# skip_create_image = true

#############
# PROVISION #
#############

slurm_version = "22.05.8"
