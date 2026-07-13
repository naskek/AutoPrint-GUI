# Check the portable license before application startup

AutoPrint reads only `config/license.json` beneath its executable or source base directory and reaches a License Decision before constructing the main `App`. This preserves the existing portable delivery model while guaranteeing that a denied installation cannot start the main GUI, BarTender COM, printer discovery, or database initialization.
