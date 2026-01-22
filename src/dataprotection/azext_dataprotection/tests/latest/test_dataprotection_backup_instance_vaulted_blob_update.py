# --------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
# pylint: disable=line-too-long
# pylint: disable=unused-import

import unittest
from azure.cli.testsdk import ScenarioTest, ResourceGroupPreparer, StorageAccountPreparer, live_only
from azure.cli.testsdk.scenario_tests import AllowLargeResponse
import time


class VaultedBlobBackupInstanceUpdateTest(ScenarioTest):
    """
    Test to verify that updating vaulted blob backup instance with new containers works correctly.
    This specifically tests the snake_case to camelCase conversion in backup datasource parameters.
    """

    @live_only()
    @AllowLargeResponse()
    @ResourceGroupPreparer(name_prefix='AzureBackupRG_clitest-dpp-vaulted-blob-update-', location='centraluseuap')
    @StorageAccountPreparer(name_prefix='clitestsa', location='centraluseuap', sku='Standard_LRS', kind='StorageV2')
    def test_dataprotection_vaulted_blob_update_containers(test, resource_group, storage_account):
        test.kwargs.update({
            'location': 'centraluseuap',
            'vaultName': 'clitest-vaulted-blob-vault',
            'policyName': 'blobpolicy',
            'dataSourceType': 'AzureBlob',
            'storageAccountName': storage_account,
            'permissionsScope': 'Resource',
            'policyRuleName': 'BackupWeekly',
            'container1': 'container-alpha',
            'container2': 'container-beta',
            'container3': 'container-gamma',
            'container4': 'container-delta'
        })

        # Create the storage account containers
        test.cmd('storage container create --name "{container1}" --account-name "{storageAccountName}" --auth-mode login')
        test.cmd('storage container create --name "{container2}" --account-name "{storageAccountName}" --auth-mode login')
        test.cmd('storage container create --name "{container3}" --account-name "{storageAccountName}" --auth-mode login')
        test.cmd('storage container create --name "{container4}" --account-name "{storageAccountName}" --auth-mode login')

        # Get storage account ID
        storage_account_info = test.cmd('storage account show --name "{storageAccountName}" -g "{rg}"').get_output_in_json()
        test.kwargs.update({'storageAccountId': storage_account_info['id']})

        # Create backup vault
        test.cmd('az dataprotection backup-vault create '
                 '-g "{rg}" --vault-name "{vaultName}" -l "{location}" '
                 '--storage-settings datastore-type="VaultStore" type="LocallyRedundant" --type SystemAssigned ',
                 checks=[
                     test.exists('identity.principalId')
                 ])

        # Wait for service principal propagation
        time.sleep(30)

        vault = test.cmd('az dataprotection backup-vault show -g "{rg}" --vault-name "{vaultName}"').get_output_in_json()
        principal_id = vault['identity']['principalId']
        test.kwargs.update({'principalId': principal_id})

        # Create backup policy
        policy_json = test.cmd('az dataprotection backup-policy get-default-policy-template --datasource-type "{dataSourceType}"').get_output_in_json()
        test.kwargs.update({"policy": policy_json})
        policy = test.cmd('az dataprotection backup-policy create -n "{policyName}" --policy "{policy}" -g "{rg}" --vault-name "{vaultName}"').get_output_in_json()
        test.kwargs.update({"policyId": policy['id']})

        # STEP 1: Initialize backup config with first two containers
        print("\n=== STEP 1: Initialize backup with containers alpha and beta ===")
        backup_config_initial = test.cmd('az dataprotection backup-instance initialize-backupconfig '
                                        '--datasource-type "{dataSourceType}" '
                                        '--vaulted-backup-containers "{container1}" "{container2}" ',
                                        checks=[
                                            test.check('object_type', 'BlobBackupDatasourceParameters'),
                                            test.check('length(containers_list)', 2)
                                        ]).get_output_in_json()
        
        print(f"Initial backup config: {backup_config_initial}")
        test.kwargs.update({"backupConfigInitial": backup_config_initial})

        # Initialize backup instance
        backup_instance_json = test.cmd('az dataprotection backup-instance initialize '
                                        '--datasource-type "{dataSourceType}" '
                                        '-l "{location}" '
                                        '--policy-id "{policyId}" '
                                        '--datasource-id "{storageAccountId}" '
                                        '--backup-config "{backupConfigInitial}"').get_output_in_json()
        
        backup_instance_guid = "b7e6f082-b310-11eb-8f55-9cfce85d4fa1"
        backup_instance_json["backup_instance_name"] = test.kwargs['storageAccountName'] + "-" + test.kwargs['storageAccountName'] + "-" + backup_instance_guid
        test.kwargs.update({
            "backupInstance": backup_instance_json,
            "backupInstanceName": backup_instance_json["backup_instance_name"]
        })

        # Grant permissions
        print("\n=== Granting MSI permissions ===")
        test.cmd('az dataprotection backup-instance update-msi-permissions '
                 '--datasource-type "{dataSourceType}" '
                 '--operation Backup '
                 '--permissions-scope "{permissionsScope}" '
                 '-g "{rg}" '
                 '--vault-name "{vaultName}" '
                 '--backup-instance "{backupInstance}" --yes')
        
        time.sleep(30)  # Wait for permissions to propagate

        # Create backup instance
        print("\n=== Creating backup instance ===")
        test.cmd('az dataprotection backup-instance validate-for-backup '
                 '-g "{rg}" '
                 '--vault-name "{vaultName}" '
                 '--backup-instance "{backupInstance}"',
                 checks=[
                     test.check('objectType', 'OperationJobExtendedInfo')
                 ])

        backup_instance_created = test.cmd('az dataprotection backup-instance create '
                                           '-g "{rg}" '
                                           '--vault-name "{vaultName}" '
                                           '--backup-instance "{backupInstance}"',
                                           checks=[
                                               test.check('properties.provisioningState', "Succeeded")
                                           ]).get_output_in_json()

        # Wait for backup instance to be configured
        test.cmd('az dataprotection backup-instance wait '
                 '-g "{rg}" '
                 '--vault-name "{vaultName}" '
                 '--backup-instance-name "{backupInstanceName}" '
                 '--timeout 120 '
                 '--custom "properties.protectionStatus.status==\'ProtectionConfigured\'"')

        # Verify initial configuration
        bi_show = test.cmd('az dataprotection backup-instance show '
                          '-g "{rg}" '
                          '--vault-name "{vaultName}" '
                          '-n "{backupInstanceName}"').get_output_in_json()
        
        print(f"\nInitial Backup Instance Policy Parameters: {bi_show['properties']['policyInfo']['policyParameters']}")
        initial_containers = bi_show['properties']['policyInfo']['policyParameters']['backupDatasourceParametersList'][0]['containersList']
        assert len(initial_containers) == 2, f"Expected 2 containers initially, got {len(initial_containers)}"
        assert test.kwargs['container1'] in initial_containers, f"Container {test.kwargs['container1']} not found"
        assert test.kwargs['container2'] in initial_containers, f"Container {test.kwargs['container2']} not found"

        # STEP 2: Update backup instance with new container list (beta, gamma, delta)
        print("\n=== STEP 2: Update backup instance with containers beta, gamma, and delta ===")
        backup_config_updated = test.cmd('az dataprotection backup-instance initialize-backupconfig '
                                        '--datasource-type "{dataSourceType}" '
                                        '--vaulted-backup-containers "{container2}" "{container3}" "{container4}" ',
                                        checks=[
                                            test.check('object_type', 'BlobBackupDatasourceParameters'),
                                            test.check('length(containers_list)', 3)
                                        ]).get_output_in_json()
        
        print(f"Updated backup config: {backup_config_updated}")
        test.kwargs.update({"backupConfigUpdated": backup_config_updated})

        # Validate the update
        print("\n=== Validating backup instance update ===")
        test.cmd('az dataprotection backup-instance validate-for-update '
                 '-g "{rg}" '
                 '--vault-name "{vaultName}" '
                 '--backup-instance-name "{backupInstanceName}" '
                 '--vaulted-blob-container-list "{backupConfigUpdated}"')

        # Perform the update with --debug to capture request ID
        print("\n=== Performing backup instance update (with --debug) ===")
        update_result = test.cmd('az dataprotection backup-instance update '
                                '-g "{rg}" '
                                '--vault-name "{vaultName}" '
                                '--backup-instance-name "{backupInstanceName}" '
                                '--vaulted-blob-container-list "{backupConfigUpdated}" '
                                '--debug',
                                checks=[
                                    test.check('properties.provisioningState', 'Succeeded')
                                ])

        # Wait for update to complete
        time.sleep(30)

        # STEP 3: Verify the update
        print("\n=== STEP 3: Verify updated container list ===")
        bi_show_updated = test.cmd('az dataprotection backup-instance show '
                                   '-g "{rg}" '
                                   '--vault-name "{vaultName}" '
                                   '-n "{backupInstanceName}"').get_output_in_json()
        
        print(f"\nUpdated Backup Instance Policy Parameters: {bi_show_updated['properties']['policyInfo']['policyParameters']}")
        updated_containers = bi_show_updated['properties']['policyInfo']['policyParameters']['backupDatasourceParametersList'][0]['containersList']
        
        assert len(updated_containers) == 3, f"Expected 3 containers after update, got {len(updated_containers)}: {updated_containers}"
        assert test.kwargs['container2'] in updated_containers, f"Container {test.kwargs['container2']} not found in updated list"
        assert test.kwargs['container3'] in updated_containers, f"Container {test.kwargs['container3']} not found in updated list"
        assert test.kwargs['container4'] in updated_containers, f"Container {test.kwargs['container4']} not found in updated list"
        assert test.kwargs['container1'] not in updated_containers, f"Container {test.kwargs['container1']} should have been removed"

        print("\n=== TEST PASSED: Container list successfully updated ===")

        # Cleanup
        test.cmd('az dataprotection backup-instance delete '
                 '-g "{rg}" '
                 '--vault-name "{vaultName}" '
                 '--backup-instance-name "{backupInstanceName}" -y')
        
        test.cmd('az dataprotection backup-vault delete -g "{rg}" --vault-name "{vaultName}" -y')
        
        # Clean up containers
        for container in [test.kwargs['container1'], test.kwargs['container2'], 
                         test.kwargs['container3'], test.kwargs['container4']]:
            test.kwargs.update({'currentContainer': container})
            test.cmd('storage container delete --name "{currentContainer}" --account-name "{storageAccountName}" --auth-mode login')
