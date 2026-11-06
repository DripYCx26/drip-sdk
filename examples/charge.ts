import { drip } from '@drip-sdk/node';

async function main() {
  const customer = await drip.createCustomer({ externalCustomerId: 'user_123' });

  await drip.charge({
    customerId: customer.id,
    meter: 'tokens',
    quantity: 1847,
  });
}

void main();
