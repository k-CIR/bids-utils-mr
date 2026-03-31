# bids-utils-mr
Providing an easy to use interface for creating a config file and running [dcm2bids](https://unfmontreal.github.io/Dcm2Bids/latest/) on [SPICE](https://k-cir.github.io/cir-wiki/SPICE/) from your local computer. This will convert raw dicoms to nifti, and order them according to BIDS.

1. Clone this repository to your project folder on SPICE.
2. Clone the repository: [serve-mr-bids](https://github.com/k-CIR/serve-mr-bids) to your local machine to connect to the service running on SPICE.

All processing is done by dcm2bids on SPICE. This repository provide an interface by serving a html-file (over ssh) to your local machine that makes it easy to construct a config-file for dcm2bids.

See the cir-wiki pages: https://k-cir.github.io/cir-wiki/mrc/mrc-bids/ for a detailed description on how to use the interface.


<img width="1614" height="1887" alt="image" src="https://github.com/user-attachments/assets/bcc95e7f-f81f-4b42-9f9e-4efff6551b80" />
