from setuptools import find_packages, setup

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="netbox-device-view",
    version="0.1.10-alpha",
    description="NetBox Device View plugin",
    packages=find_packages(),
    author="Peter Baumert",
    include_package_data=True,
    zip_safe=False,
    install_requires=[],
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/peterbaumert/netbox-device-view",
    keywords=["netbox", "netbox-plugin"],
    classifiers=[
        "Framework :: Django",
        "Programming Language :: Python :: 3",
    ],
)
