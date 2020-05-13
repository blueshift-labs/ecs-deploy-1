#!/bin/bash
if [ $# -eq 0 ]; then
    echo "No arguments provided. Arg1 is docker tag name. e.g. 20.5.1"
    exit 1
fi

TAG=ecs_deploy:$1

# Build docker image
docker build -t $TAG .

# Tag & push to production registry
echo "----------- Pushing to prod -> $TAG"
docker tag $TAG docker-registry.blueshift.vpc/$TAG
docker push docker-registry.blueshift.vpc/$TAG

# Tag & push to staging registry
echo "----------- Pushing to staging -> $TAG"
docker tag $TAG staging-docker-registry.bsftstaging.vpc/$TAG
docker push staging-docker-registry.bsftstaging.vpc/$TAG