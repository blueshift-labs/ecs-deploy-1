#!/bin/bash
if [ $# -eq 0 ]; then
    echo "No arguments provided. Arg1 is docker tag name. e.g. 20.5.1"
    exit 1
fi

TAG=$1

# Build docker image
docker build -t ecs_deploy:$TAG .

# Tag & push to production registry
echo "----------- Pushing to prod -----------"
docker tag ecs_deploy:$TAG docker-registry.blueshift.vpc/ecs_deploy:$TAG
docker push docker-registry.blueshift.vpc/ecs_deploy:$TAG

# Tag & push to staging registry
echo "----------- Pushing to staging -----------"
docker tag ecs_deploy:$TAG staging-docker-registry.bsftstaging.vpc/ecs_deploy:$TAG
docker push staging-docker-registry.bsftstaging.vpc/ecs_deploy:$TAG