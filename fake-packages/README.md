Why fake-packages?
====

As a security precaution, travis-ci maintains a whitelist of packages that can
be installed for tests.

https://github.com/travis-ci/apt-package-whitelist#package-approval-process

Any package not on the whitelist cannot be installed for testing. The Ubuntu
packages `python-netifaces` and `python-netsnmp` are not on the whitelist. As
well, the pip packages for `netifaces` and `netsnmp` cannot build because
*their* dependencies are not whitelisted either, e.g. libsnmp-dev.

Since these packages involve network discovery and system introspection
(potential security risks), it is unlikely that travis would whitelist these
packages or their dependencies in the future.

So, to work around this limitation, this directory contains minimal skeleton
versions of packages that we need to test within travis-ci.

The `.travis.yml` file calls `build` with a custom PYTHONPATH that includes
this directory. The `build` script itself should remain unmodified so that
developer workstations can still run unit tests during pre-commit hooks with
the true versions of `netifaces` and `netsnmp`.
