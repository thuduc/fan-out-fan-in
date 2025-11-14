Add support for XML processing instructions (PIs) for valuation request XML
- An example of a valuation containing processing instructions can be found in @resource/request6.xml
- The @resource/request6.xml contains 2 PIs:
<?vnvs $vn_version = "27.3.0" ?>    
<?vnvs $NPATH = "10000" ?>    
- Add support for processing instructions by replacing all references to PIs variables with their value. For example, in @resource/request6.xml, the following XML element references the $NPATH attribute of a PI:
                    <size>$NPATH</nPath>
- After replacement, this XML element should look like below, since $NPATH = "10000" in the PI:
                    <size>10000</nPath>
- Create a test case to ensure the implementation is correct