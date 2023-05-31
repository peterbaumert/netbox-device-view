from setuptools import find_packages, setup

setup(
    name="netbox-device-view",
    version="0.1",
    description="NetBox Device View plugin",
    packages=find_packages(),
    author='Peter Baumert',
    include_package_data=True,
    zip_safe=False,
    install_requires=[],
)
