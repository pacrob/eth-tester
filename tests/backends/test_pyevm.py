import pytest

from eth.constants import (
    POST_MERGE_DIFFICULTY,
    POST_MERGE_MIX_HASH,
    POST_MERGE_NONCE,
)
from eth.vm.forks import (
    BerlinVM,
    CancunVM,
    FrontierVM,
    GrayGlacierVM,
    LondonVM,
    ParisVM,
    PragueVM,
    ShanghaiVM,
)
from eth_abi import (
    abi,
)
from eth_account import (
    Account,
)
from eth_typing import (
    HexStr,
)
from eth_utils import (
    ValidationError as EthUtilsValidationError,
    encode_hex,
    is_hexstr,
    to_bytes,
    to_hex,
    to_wei,
)
from eth_utils.toolz import (
    merge,
)

from eth_tester import (
    EthereumTester,
    PyEVMBackend,
)
from eth_tester.backends.pyevm.main import (
    GENESIS_DIFFICULTY,
    GENESIS_MIX_HASH,
    GENESIS_NONCE,
    generate_genesis_state_for_keys,
    get_default_account_keys,
    get_default_genesis_params,
    setup_tester_chain,
)
from eth_tester.backends.pyevm.utils import (
    is_supported_pyevm_version_available,
)
from eth_tester.exceptions import (
    BlockNotFound,
    ValidationError,
)
from eth_tester.normalization.outbound import (
    normalize_withdrawal,
)
from eth_tester.utils.backend_testing import (
    SIMPLE_TRANSACTION,
    BaseTestBackendDirect,
)

ZERO_ADDRESS_HEX = "0x0000000000000000000000000000000000000000"
MNEMONIC = "test test test test test test test test test test test junk"


@pytest.fixture
def eth_tester():
    if not is_supported_pyevm_version_available():
        pytest.skip("PyEVM is not available")
    backend = PyEVMBackend()
    return EthereumTester(backend=backend)


@pytest.fixture
def accounts_from_mnemonic():
    return [
        "0x1e59ce931B4CFea3fe4B875411e280e173cB7A9C",
        "0xc89D42189f0450C2b2c3c61f58Ec5d628176A1E7",
        "0x318b469BBa396AEc2C60342F9441be36A1945174",
    ]


def test_custom_virtual_machines():
    if not is_supported_pyevm_version_available():
        pytest.skip("PyEVM is not available")

    backend = PyEVMBackend(
        vm_configuration=(
            (0, FrontierVM),
            (3, ParisVM),
        )
    )

    # This should be a FrontierVM block
    VM_at_2 = backend.chain.get_vm_class_for_block_number(2)
    # This should be a ParisVM block
    VM_at_3 = backend.chain.get_vm_class_for_block_number(3)

    assert FrontierVM.__name__ == "FrontierVM"
    assert VM_at_2.__name__ == FrontierVM.__name__

    assert ParisVM.__name__ == "ParisVM"
    assert VM_at_3.__name__ == ParisVM.__name__

    # Right now, just test that EthereumTester doesn't crash
    # Maybe some more sophisticated test to make sure the VMs are set correctly?
    # We should to make sure the VM config translates all the way to the main
    #   tester, maybe with a custom VM that hard-codes some block value? that can
    #   be found with tester.get_block_by_number()?
    EthereumTester(backend=backend)


@pytest.mark.parametrize(
    "vm_class_missing_the_field,vm_class_with_new_field,new_field",
    (
        (BerlinVM, LondonVM, "base_fee_per_gas"),
        (ParisVM, ShanghaiVM, "withdrawals"),
        (ParisVM, ShanghaiVM, "withdrawals_root"),
        (ShanghaiVM, CancunVM, "blob_gas_used"),
        (ShanghaiVM, CancunVM, "excess_blob_gas"),
        (ShanghaiVM, CancunVM, "parent_beacon_block_root"),
        (CancunVM, PragueVM, "requests_hash"),
    ),
)
def test_newly_introduced_block_fields_at_fork_transition(
    vm_class_missing_the_field,
    vm_class_with_new_field,
    new_field,
):
    if not is_supported_pyevm_version_available():
        pytest.skip("PyEVM is not available")

    vm_configuration = ((0, vm_class_missing_the_field), (1, vm_class_with_new_field))
    backend = PyEVMBackend(vm_configuration=vm_configuration)

    # test that the field / key does not exist pre-fork
    with pytest.raises(KeyError):
        backend.get_block_by_number(0)[new_field]

    tester = EthereumTester(backend=backend)

    # Test that outbound block validation doesn't break by getting a block.
    pre_fork_block = tester.get_block_by_number(0)

    # Test that outbound block normalization removes fork-specific field.
    with pytest.raises(KeyError):
        pre_fork_block[new_field]

    # test that the field exists at fork transition
    backend.get_block_by_number("pending")[new_field]
    post_fork_block = tester.get_block_by_number("pending")
    assert post_fork_block[new_field] is not None


def test_london_configuration():
    if not is_supported_pyevm_version_available():
        pytest.skip("PyEVM is not available")

    backend = PyEVMBackend(vm_configuration=((0, LondonVM),))

    assert backend.get_block_by_number(0)["base_fee_per_gas"] == 1000000000

    EthereumTester(backend=backend)


def test_apply_withdrawals():
    if not is_supported_pyevm_version_available():
        pytest.skip("PyEVM is not available")

    backend = PyEVMBackend(vm_configuration=((0, ShanghaiVM),))

    tester = EthereumTester(backend=backend)

    withdrawals = [
        {
            "index": 0,
            "validator_index": 0,
            "address": f"0x{'01' * 20}",
            "amount": 100,
        },
        {
            "index": 2**64 - 1,
            "validator_index": 2**64 - 1,
            "address": b"\x02" * 20,
            "amount": 2**64 - 1,
        },
    ]
    backend.apply_withdrawals(withdrawals)

    mined_block = tester.get_block_by_number("latest")
    assert (
        mined_block["withdrawals"] == normalize_withdrawal(withdrawal)
        for withdrawal in withdrawals
    )
    # withdrawal amounts are in gwei, balance is measured in wei
    assert backend.get_balance(b"\x01" * 20) == 100 * 10**9  # 100 gwei
    assert (
        backend.get_balance(b"\x02" * 20) == (2**64 - 1) * 10**9
    )  # 2**64 - 1 gwei

    assert (
        mined_block["withdrawals_root"]
        == "0xbb49834f60c98815399dfb1a3303cc0f80984c4c7533ecf326bc343d8109127e"
    )


class TestPyEVMBackendDirect(BaseTestBackendDirect):
    def test_generate_custom_genesis_state(self):
        state_overrides = {"balance": to_wei(900000, "ether")}
        invalid_overrides = {"gato": "con botas"}

        # Test creating a specific number of accounts
        account_keys = get_default_account_keys(quantity=2)
        assert len(account_keys) == 2
        account_keys = get_default_account_keys(quantity=10)
        assert len(account_keys) == 10

        # Test the underlying state merging functionality
        genesis_state = generate_genesis_state_for_keys(
            account_keys=account_keys, overrides=state_overrides
        )
        assert len(genesis_state) == len(account_keys) == 10
        for _public_address, account_state in genesis_state.items():
            assert account_state["balance"] == state_overrides["balance"]
            assert account_state["code"] == b""

        # Only existing default genesis state keys can be overridden
        with pytest.raises(ValueError):
            generate_genesis_state_for_keys(
                account_keys=account_keys, overrides=invalid_overrides
            )

        # Use staticmethod state overriding
        genesis_state = PyEVMBackend.generate_genesis_state(
            overrides=state_overrides, num_accounts=3
        )
        assert len(genesis_state) == 3
        for _public_address, account_state in genesis_state.items():
            assert account_state["balance"] == state_overrides["balance"]
            assert account_state["code"] == b""

        # Only existing default genesis state keys can be overridden
        with pytest.raises(ValueError):
            PyEVMBackend.generate_genesis_state(overrides=invalid_overrides)

    def test_override_genesis_state(self):
        state_overrides = {"balance": to_wei(900000, "ether")}
        test_accounts = 3

        # Initialize PyEVM backend with custom genesis state
        genesis_state = PyEVMBackend.generate_genesis_state(
            overrides=state_overrides, num_accounts=test_accounts
        )

        # Test the correct number of accounts are created with the specified
        # balance override
        pyevm_backend = PyEVMBackend(genesis_state=genesis_state)
        assert len(pyevm_backend.account_keys) == test_accounts
        for private_key in pyevm_backend.account_keys:
            account = private_key.public_key.to_canonical_address()
            balance = pyevm_backend.get_balance(account=account)
            assert balance == state_overrides["balance"]

        # Test integration with EthereumTester
        tester = EthereumTester(backend=pyevm_backend)
        for private_key in pyevm_backend.account_keys:
            account = private_key.public_key.to_checksum_address()
            balance = tester.get_balance(account=account)
            assert balance == state_overrides["balance"]

    def test_from_mnemonic(self, accounts_from_mnemonic):
        # Initialize PyEVM backend using MNEMONIC, num_accounts,
        # and state overrides (balance)
        num_accounts = 3
        balance = to_wei(15, "ether")  # Give each account 15 Eth
        pyevm_backend = PyEVMBackend.from_mnemonic(
            MNEMONIC,
            num_accounts=num_accounts,
            genesis_state_overrides={"balance": balance},
        )

        # Test integration with EthereumTester
        tester = EthereumTester(backend=pyevm_backend)

        actual_accounts = tester.get_accounts()
        assert len(actual_accounts) == num_accounts

        assert all(acct in accounts_from_mnemonic for acct in actual_accounts)

        for i in range(0, num_accounts):
            actual = actual_accounts[i]
            expected = accounts_from_mnemonic[i]
            assert actual.lower() == expected.lower()
            assert tester.get_balance(account=actual) == balance

    def test_from_mnemonic_override_hd_path(self, accounts_from_mnemonic):
        # Initialize PyEVM backend using MNEMONIC, num_accounts,
        # and custom hd_path
        num_accounts = 3
        pyevm_backend = PyEVMBackend.from_mnemonic(
            MNEMONIC,
            num_accounts=num_accounts,
            hd_path="m/44'/60'/7'",
        )

        # Each of these accounts stems from the MNEMONIC, but with a different hd_path
        expected_accounts = [
            "0x9aEFA413550e6Ae8690642994310d13dDA248b6b",
            "0xcBA8AFA62949343128FE341C3C7F6b119dF78249",
            "0x2C7DdecbF4555dd2220eF92e21B2912342655845",
        ]

        # Test integration with EthereumTester
        tester = EthereumTester(backend=pyevm_backend)

        actual_accounts = tester.get_accounts()
        assert len(actual_accounts) == num_accounts

        assert not any(acct in accounts_from_mnemonic for acct in actual_accounts)
        assert all(acct in expected_accounts for acct in actual_accounts)

    def test_generate_custom_genesis_parameters(self):
        # Establish parameter overrides, for example a custom genesis gas limit
        param_overrides = {"gas_limit": 4750000}

        # Test the underlying default parameter merging functionality
        genesis_params = get_default_genesis_params(overrides=param_overrides)
        assert genesis_params["gas_limit"] == param_overrides["gas_limit"]

        # Use the staticmethod to generate custom genesis parameters
        genesis_params = PyEVMBackend.generate_genesis_params(param_overrides)
        assert genesis_params["gas_limit"] == param_overrides["gas_limit"]

        # Only existing default genesis parameter keys can be overridden
        invalid_overrides = {"gato": "con botas"}
        with pytest.raises(ValueError):
            PyEVMBackend.generate_genesis_params(overrides=invalid_overrides)

    def test_override_genesis_parameters(self):
        # Establish a custom gas limit
        param_overrides = {
            "gas_limit": 4750000,
        }
        block_one_gas_limit = param_overrides["gas_limit"]

        # Initialize PyEVM backend with custom genesis parameters
        genesis_params = PyEVMBackend.generate_genesis_params(overrides=param_overrides)
        pyevm_backend = PyEVMBackend(genesis_parameters=genesis_params)
        genesis_block = pyevm_backend.get_block_by_number(0)
        assert genesis_block["gas_limit"] == param_overrides["gas_limit"]
        pending_block_one = pyevm_backend.get_block_by_number("pending")
        assert pending_block_one["gas_limit"] == block_one_gas_limit

        # Integrate with EthereumTester
        tester = EthereumTester(backend=pyevm_backend)
        genesis_block = tester.get_block_by_number(0)
        assert genesis_block["gas_limit"] == param_overrides["gas_limit"]
        pending_block_one = tester.get_block_by_number("pending")
        assert pending_block_one["gas_limit"] == block_one_gas_limit

    def test_send_transaction_invalid_from(self, eth_tester):
        accounts = eth_tester.get_accounts()
        assert accounts, "No accounts available for transaction sending"

        with pytest.raises(ValidationError, match=r'No valid "from" key was provided'):
            self._send_and_check_transaction(
                eth_tester, SIMPLE_TRANSACTION, ZERO_ADDRESS_HEX
            )

    def test_pending_block_not_found_when_fetched_by_number(self, eth_tester):
        # assert `latest` block can be fetched by number
        latest_block_num = eth_tester.get_block_by_number("latest")["number"]
        assert isinstance(latest_block_num, int)
        eth_tester.get_block_by_number(latest_block_num)

        # assert `pending` block cannot be fetched by number
        pending_block_num = eth_tester.get_block_by_number("pending")["number"]
        assert isinstance(pending_block_num, int)
        assert pending_block_num == latest_block_num + 1

        with pytest.raises(BlockNotFound):
            eth_tester.get_block_by_number(pending_block_num)

    def test_pyevm_backend_with_custom_vm_configuration_pow_to_pos(self):
        vm_config = (
            (0, GrayGlacierVM),
            (3, ParisVM),
        )

        pyevm_backend = PyEVMBackend(vm_configuration=vm_config)
        tester = EthereumTester(backend=pyevm_backend)

        # assert genesis block was created with pre-merge, PoW genesis values
        genesis_block = tester.get_block_by_number(0)

        assert genesis_block["difficulty"] == GENESIS_DIFFICULTY
        assert genesis_block["nonce"] == encode_hex(GENESIS_NONCE)
        assert genesis_block["mix_hash"] == encode_hex(GENESIS_MIX_HASH)

        tester.mine_blocks(3)

        # assert smooth transition to PoS with expected values
        third_block = tester.get_block_by_number(3)
        assert third_block["difficulty"] == POST_MERGE_DIFFICULTY
        assert third_block["nonce"] == encode_hex(POST_MERGE_NONCE)

        # assert not empty mix_hash
        third_block_mix_hash = third_block["mix_hash"]
        assert is_hexstr(third_block_mix_hash)
        assert third_block_mix_hash != encode_hex(POST_MERGE_MIX_HASH)

    def test_pyevm_backend_with_custom_vm_configuration_post_merge(self):
        vm_config = ((0, ParisVM),)

        _acct_keys, chain = setup_tester_chain(vm_configuration=vm_config)

        # assert genesis block was created with post-merge, PoS genesis values
        genesis_block = chain.get_canonical_block_by_number(0)
        assert genesis_block.header.difficulty == POST_MERGE_DIFFICULTY
        assert genesis_block.header.nonce == POST_MERGE_NONCE
        assert genesis_block.header.mix_hash == POST_MERGE_MIX_HASH

    def test_eth_get_storage_at(self):
        # add storage to accounts in the genesis block
        state_overrides = {
            "storage": {
                1: 1,
                2: 2,
            }
        }

        genesis_state = PyEVMBackend.generate_genesis_state(
            overrides=state_overrides, num_accounts=3
        )
        pyevm_backend = PyEVMBackend(genesis_state=genesis_state)
        tester = EthereumTester(backend=pyevm_backend)

        accounts = tester.get_accounts()
        assert len(accounts) == 3

        for acct in accounts:
            assert tester.get_storage_at(acct, HexStr("0x0")) == f"0x{'00'*32}"
            assert tester.get_storage_at(acct, HexStr("0x1")) == f"0x{'00'*31}01"
            assert tester.get_storage_at(acct, HexStr("0x2")) == f"0x{'00'*31}02"

    # --- cancun network upgrade --- #

    BLOB_TEXT = "We are the music makers, And we are the dreamers of dreams."
    ENCODED_BLOB_TEXT = abi.encode(["string"], [BLOB_TEXT])

    BLOB_TX_FOR_SIGNING = {
        "type": 3,
        "chainId": 131277322940537,
        "value": 0,
        "gas": 200_000,
        "maxFeePerGas": 10**10,
        "maxPriorityFeePerGas": 10**10,
        "maxFeePerBlobGas": 10**10,
        "nonce": 0,
    }
    set_code_tx = merge(BLOB_TX_FOR_SIGNING, {"type": 4})
    set_code_tx.pop("maxFeePerBlobGas", None)
    SET_CODE_TX_FOR_SIGNING = set_code_tx

    def test_send_raw_transaction_valid_blob_transaction(self, eth_tester):
        pkey = eth_tester.backend.account_keys[0]
        acct = Account.from_key(pkey)

        tx = self.BLOB_TX_FOR_SIGNING.copy()
        tx["from"] = acct.address
        tx["to"] = eth_tester.get_accounts()[1]

        # Blobs contain 4096 32-byte field elements. Subtract the length of the encoded
        # text divided into 32-byte chunks from 4096 and pad the rest with zeros.
        blob_data = (
            b"\x00" * 32 * (4096 - len(self.ENCODED_BLOB_TEXT) // 32)
        ) + self.ENCODED_BLOB_TEXT

        signed = acct.sign_transaction(tx, blobs=[blob_data])
        tx_hash = eth_tester.send_raw_transaction(to_hex(signed.raw_transaction))
        assert eth_tester.get_transaction_by_hash(tx_hash)

    def test_send_raw_transaction_invalid_blob_transaction(self, eth_tester):
        pkey = eth_tester.backend.account_keys[0]
        acct = Account.from_key(pkey)

        tx = self.BLOB_TX_FOR_SIGNING.copy()
        tx["from"] = acct.address
        tx["to"] = eth_tester.get_accounts()[1]

        blob_data = (
            b"\x00" * 32 * (4096 - len(self.ENCODED_BLOB_TEXT) // 32)
        ) + self.ENCODED_BLOB_TEXT[
            :-1
        ]  # only 1 byte short -- invalid

        with pytest.raises(EthUtilsValidationError):
            acct.sign_transaction(tx, blobs=[blob_data])

    @pytest.mark.parametrize("send_raw", {True, False}, ids=lambda x: f"send_raw={x}")
    def test_send_set_code_transaction_clear_delegation_with_send_signed(
        self,
        send_raw,
    ):
        # set `1` at storage slot `0`
        code = to_bytes(hexstr="0x6001600055")

        genesis_state = PyEVMBackend.generate_genesis_state(num_accounts=10)
        # grab the 10th account and make it the contract acct (set code=code)
        contract_addr_bytes = list(genesis_state.keys())[-1]
        genesis_state[contract_addr_bytes]["code"] = code

        backend = PyEVMBackend(genesis_state=genesis_state)
        eth_tester = EthereumTester(backend=backend)

        contract_addr = f"0x{contract_addr_bytes.hex()}"
        assert eth_tester.get_code(contract_addr) == f"0x{code.hex()}"

        pkey = eth_tester.backend.account_keys[0]
        acct = Account.from_key(pkey)

        nonce = eth_tester.get_nonce(acct.address)
        auth = {
            "chainId": eth_tester.backend.chain.chain_id,
            "address": contract_addr,
            "nonce": nonce + 1,
        }
        tx = self.SET_CODE_TX_FOR_SIGNING.copy()
        tx["to"] = acct.address
        tx["nonce"] = nonce
        signed_auth = acct.sign_authorization(auth).model_dump(by_alias=True)
        tx["authorizationList"] = [signed_auth]

        if send_raw:
            signed_tx = acct.sign_transaction(tx)
            tx_hash = eth_tester.send_raw_transaction(to_hex(signed_tx.raw_transaction))
        else:
            signed_auth.update(
                {
                    "chain_id": signed_auth.pop("chainId"),
                    "y_parity": signed_auth.pop("yParity"),
                }
            )
            tx.update(
                {
                    # camelCase -> snake_case
                    "chain_id": tx.pop("chainId"),
                    "max_priority_fee_per_gas": tx.pop("maxPriorityFeePerGas"),
                    "max_fee_per_gas": tx.pop("maxFeePerGas"),
                    "authorization_list": tx.pop("authorizationList"),
                    "from": acct.address,
                }
            )
            tx_hash = eth_tester.send_transaction(tx)

        assert eth_tester.get_transaction_by_hash(tx_hash)
        assert eth_tester.get_code(acct.address) == (
            # assert set to delegation prefix + contract address (0xef0001...)
            "0xef0100"
            + contract_addr[2:]
        )
        assert (
            # assert set `1` at storage slot `0`
            eth_tester.get_storage_at(acct.address, HexStr("0x0"))
            == "0x" + "00" * 31 + "01"
        )

        # clear code and send via ``send_signed`` to test the
        # ``transaction_builder.new_signed_set_code_transaction()`` api
        reset_code_auth = {
            "chainId": eth_tester.backend.chain.chain_id,
            "address": "0x" + "00" * 20,
            "nonce": nonce + 3,
        }
        auth_dict = acct.sign_authorization(reset_code_auth).model_dump(by_alias=True)
        reset_code_tx = {
            "chainId": eth_tester.backend.chain.chain_id,
            "to": acct.address,
            "gas": 200_000,
            "nonce": nonce + 2,
            "maxFeePerGas": 10**10,
            "maxPriorityFeePerGas": 10**10,
            "authorizationList": [auth_dict],
        }
        signed_reset_code_tx = acct.sign_transaction(reset_code_tx)
        auth_dict.update(
            {"chain_id": auth_dict.pop("chainId"), "y_parity": auth_dict.pop("yParity")}
        )
        reset_code_tx.update(
            {
                "chain_id": reset_code_tx.pop("chainId"),
                "from": acct.address,
                "max_fee_per_gas": reset_code_tx.pop("maxFeePerGas"),
                "max_priority_fee_per_gas": reset_code_tx.pop("maxPriorityFeePerGas"),
                "authorization_list": reset_code_tx.pop("authorizationList"),
                "r": signed_reset_code_tx.r,
                "s": signed_reset_code_tx.s,
                "v": signed_reset_code_tx.v,
            }
        )

        eth_tester._add_transaction_to_pending_block(reset_code_tx, "send_signed")
        eth_tester.mine_block()
        assert eth_tester.get_code(acct.address) == "0x"

    def test_eth_call_does_not_require_a_known_account(self, eth_tester):
        # `eth_call` should not require the `from` address to be a known account
        # as it does not change the state of the blockchain
        acct = Account.create()

        # fund acct
        eth_tester.send_transaction(
            {
                "from": eth_tester.get_accounts()[0],
                "to": acct.address,
                "value": 10**18,
                "gas": 21000,
            }
        )

        result = eth_tester.call(
            {
                "from": acct.address,
                "to": eth_tester.get_accounts()[0],
                "data": "0x",
            }
        )

        assert result == "0x"
