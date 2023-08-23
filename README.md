# AppFlowy-Binaries
⚠️ This repository is exclusively for internal release package builds. Please refrain from creating PRs.


Clone this repository to your local machine. 
```shell
git clone git@github.com:AppFlowy-IO/AppFlowy-Builder.git
```

and then you can use tag to trigger the build process. 
```shell
git tag -a 0.0.1_main && git push origin 0.0.1_main
```
It will initiate the build process that uses the main branch to construct the AppFlowy desktop application with version number 0.0.1. Each build will contain the latest commit from the specific branch. The package will be built for different environments, such as prod and stage.

You can view the build process [here](https://github.com/AppFlowy-IO/AppFlowy-Builder/actions)

> * Stage (Staging): This is a pre-production environment used for testing. After development, the code is deployed to this environment to simulate how it would work in the production environment.
> * Prod (Production): This is the live environment where the application runs for the end-users.
