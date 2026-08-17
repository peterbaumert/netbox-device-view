# Changelog

## [0.3.0](https://github.com/peterbaumert/netbox-device-view/compare/v0.3.0...v0.3.0) (2026-05-03)


### Features

* add SVG renderer with render_mode field and 58 new tests ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* add YAML layout examples for 10 device types ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* add YAML layout system with CSS Grid renderer ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* click navigates to port detail; tooltip adds Trace link ([5c1a5d8](https://github.com/peterbaumert/netbox-device-view/commit/5c1a5d8df9f6749c800960e6acb8914101291220))
* improve device view list table — linked device type, count, layout badges, render mode ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* order virtual chassis panels by physical rack position respecting desc_units ([e36d323](https://github.com/peterbaumert/netbox-device-view/commit/e36d3230df3db8066b39f38c412bdad557f3e0a0))
* **svg:** add partial connection indicator for SVG renderer ([#62](https://github.com/peterbaumert/netbox-device-view/issues/62)) ([5927254](https://github.com/peterbaumert/netbox-device-view/commit/59272545d9638e3a310478c8b7364c88000ba6ee)), closes [#61](https://github.com/peterbaumert/netbox-device-view/issues/61)
* switch tooltip to popover so Trace link is clickable ([be54797](https://github.com/peterbaumert/netbox-device-view/commit/be547977abf641c85b3cd0fe47da1280e48d95c0))


### Bug Fixes

* 17 numeric ports only ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* apply detail/trace link changes to ports.html ([a7fa915](https://github.com/peterbaumert/netbox-device-view/commit/a7fa915768ed610435bfeaf6b4987baeec31938d))
* detect horizontal col_span in css_to_yaml; add C9500-24Y4C YAML layout ([e361c46](https://github.com/peterbaumert/netbox-device-view/commit/e361c463fc4e00f64227919b6ec4df8997802079))
* equal 500ms show/hide delay on popovers to prevent overlap ([92cbaf5](https://github.com/peterbaumert/netbox-device-view/commit/92cbaf5e42210b05fa6ac88c4ec1f1f9d963280f))
* keep popover open when cursor moves into it ([b837b5c](https://github.com/peterbaumert/netbox-device-view/commit/b837b5c2ab150fee6099a6a1aba09f74acac6465))
* remove unused pytest import in test_css_to_yaml ([dff5c4f](https://github.com/peterbaumert/netbox-device-view/commit/dff5c4f311ce7247118fc1a7167b422ecfc3a637))
* scope partial-connection indicator to correct chassis panel in virtual chassis ([4522dbe](https://github.com/peterbaumert/netbox-device-view/commit/4522dbebf77b99895b80993309829f1b2d8ad965))
* show virtual chassis name in device view card header ([013c741](https://github.com/peterbaumert/netbox-device-view/commit/013c741df7a08fb79c4bb126cd4fd83de7b34926))
* suppress partial-connection indicator on patch panel ports ([a0a81c2](https://github.com/peterbaumert/netbox-device-view/commit/a0a81c235f7856d4caa7d5f49f2e97940b0aacf8))
* SVG JS — escapejs, nocolor pattern and pointer-events; patch panel SVG rendering on device tab ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* use window.Popover (NetBox global) and manually init CSS grid popovers ([f111350](https://github.com/peterbaumert/netbox-device-view/commit/f1113509bb8ba8817e112d7dd6cf53b13a14f2d1))
* YAML examples — correct stylenames, interface prefixes, C2960X layout ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))


### Documentation

* add C9200-48PXG layout examples with C9200-NM-4X variant ([8a8963b](https://github.com/peterbaumert/netbox-device-view/commit/8a8963b0da9e4fa22e2ee72549fb161949a61ea6))
* add YAML layout schema reference and update README ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* correct C9200-48PXG port types for ports 41-48 ([db119eb](https://github.com/peterbaumert/netbox-device-view/commit/db119eb49b182f844f983cc1af8a89e334810fa8))
* redesign port-status SVG example — vertical card layout ([27864bd](https://github.com/peterbaumert/netbox-device-view/commit/27864bd0d59bc7e6e734e50fc00eb0e2d654a814))
* set up MkDocs Material site with port status SVG example ([#63](https://github.com/peterbaumert/netbox-device-view/issues/63)) ([154b7e6](https://github.com/peterbaumert/netbox-device-view/commit/154b7e6bf4bccb8ef82ad47adbc5eaf746626da4))


### Miscellaneous Chores

* release 0.3.0 ([19f0308](https://github.com/peterbaumert/netbox-device-view/commit/19f03080152fc3e37ec6cfe011b00f7610f894a5))
* trigger release-please for 0.3.0 ([0393c19](https://github.com/peterbaumert/netbox-device-view/commit/0393c19b33975c35f476e4ca8da416f1575d0af3))

## [0.2.0](https://github.com/peterbaumert/netbox-device-view/compare/v0.1.15...v0.2.0) (2026-04-03)


### Features

* add device_tab_position config ([cc7405a](https://github.com/peterbaumert/netbox-device-view/commit/cc7405ac3ebd89dcf9b0253165c59db2756a7dd7))
* show live stylename preview on add/edit form ([cf9d615](https://github.com/peterbaumert/netbox-device-view/commit/cf9d6159da82a3bd845c949b23300bf9f194432d))


### Documentation

* rewrite README and add COMPATIBILITY.md ([0171d33](https://github.com/peterbaumert/netbox-device-view/commit/0171d33ed23abf83a1345e4cc456b0c35a1ff78d))
